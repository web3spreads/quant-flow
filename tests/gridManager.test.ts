/** GridManager 资金安全路径测试：紧急平仓校验、增量同步防误判、手术式减仓、
 * 重建闸门与开平仓闭环保全。
 *
 * 全部离线：交易所行为由 FakeGridClient 模拟，checkOrderSuccess 复用真实实现
 * 保证内层 statuses 校验语义与线上一致。
 */
import { describe, expect, it } from "vitest";
import path from "node:path";
import { GridManager } from "../src/trading/gridManager.js";
import { GridPnLTracker } from "../src/trading/gridPnl.js";
import { GridLevel, GridLevelState } from "../src/utils/gridMath.js";
import { toDecimal } from "../src/utils/precision.js";
import {
  FILLED_ORDER,
  FakeGridClient,
  FakeGridOrderManager,
  QUIET_LOGGER,
  REJECTED_ORDER,
  RO_NETTED_REJECTED_ORDER,
  makeQuietLogger,
  makeTempDir,
} from "./support.js";
import type { Dict } from "../src/trading/client.js";

function makeManager(options: { client?: FakeGridClient; netting?: boolean } = {}): {
  manager: GridManager;
  client: FakeGridClient;
  om: FakeGridOrderManager;
} {
  const client = options.client ?? new FakeGridClient();
  const om = new FakeGridOrderManager(client);
  const manager = new GridManager({
    orderManager: om as never,
    logger: QUIET_LOGGER,
    stateFile: path.join(makeTempDir(), "grid_state.json"),
    nettingAttributionEnabled: options.netting ?? true,
  });
  return { manager, client, om };
}

function makeFilledLevel(levelId = "L0", side: "LONG" | "SHORT" = "LONG", price = "100", amount = "0.5"): GridLevel {
  const level = new GridLevel({
    id: levelId,
    price: toDecimal(price),
    amount: toDecimal("50"),
    side,
    state: GridLevelState.OPEN_FILLED,
  });
  level.openFillPrice = toDecimal(price);
  level.openFillAmount = toDecimal(amount);
  level.openFillTime = 1000.0;
  return level;
}

const anyOf = (m: GridManager) => m as never as Dict;


describe("紧急平仓校验", () => {
  // 平仓失败绝不能记成成功：风控状态一旦被清，平不掉的仓位就再没人接管。
  it("成功才清理状态；失败则保留层级并落盘待重试标记", async () => {
    for (const ok of [true, false]) {
      const { manager, client } = makeManager();
      manager.gridLevels.ETH = [makeFilledLevel()];
      manager.pnlTrackers.ETH = new GridPnLTracker();
      client.positions = [{ coin: "ETH", szi: "0.5" }];
      client.emergencyResults = [[ok, ok ? FILLED_ORDER : { status: "error", message: "限流" }]];

      expect(await manager.emergencyCloseAll("ETH", "STOP_LOSS 测试"), String(ok)).toBe(ok);
      const pending = (manager.state.pending_emergency_close as Dict | undefined)?.ETH;
      if (ok) {
        expect(manager.gridLevels.ETH).toBeUndefined();
        expect(pending).toBeUndefined();
      } else {
        expect(manager.gridLevels.ETH).toBeDefined(); // 风控状态保留
        expect(pending).toBe("STOP_LOSS 测试");
      }
    }
  });

  it("待重试：持仓已消失只做状态收尾，仍有持仓才重走完整平仓", async () => {
    for (const held of [false, true]) {
      const { manager, client } = makeManager();
      manager.gridLevels.ETH = [makeFilledLevel()];
      (manager.state.pending_emergency_close as Dict) = { ETH: "上轮失败" };
      client.positions = held ? [{ coin: "ETH", szi: "0.5" }] : [];
      client.emergencyResults = [[true, FILLED_ORDER]];

      await manager.retryPendingEmergencyClose("ETH");
      expect(client.emergencyCalls.length, String(held)).toBe(held ? 1 : 0);
      expect((manager.state.pending_emergency_close as Dict).ETH, String(held)).toBeUndefined();
      if (!held) expect(manager.gridLevels.ETH).toBeUndefined();
    }
  });
});

describe("增量同步防误判", () => {
  const pendingLevel = (): GridLevel => {
    const level = new GridLevel({
      id: "L0", price: toDecimal("99"), amount: toDecimal("50"), side: "LONG",
      state: GridLevelState.OPEN_PENDING,
    });
    level.openOrderId = 12345;
    return level;
  };

  // 「查不到」不等于「没有」：任一查询故障都必须整轮跳过，
  // 否则已成交的层级会被误判成被撤销、打回 IDLE 重挂，等于凭空多开一手。
  it("挂单或成交记录查询失败：层级状态不变、不重新挂单", async () => {
    const cases: Array<[string, (c: FakeGridClient) => void]> = [
      ["挂单查询失败", (c) => (c.openOrders = null)],
      ["订单已不在挂单列表但 fills 接口故障", (c) => {
        c.openOrders = [];
        c.fills = null;
      }],
    ];
    for (const [name, setup] of cases) {
      const { manager, client, om } = makeManager();
      const level = pendingLevel();
      manager.gridLevels.ETH = [level];
      setup(client);
      await manager.syncGridIncremental("ETH");
      expect(level.state, name).toBe(GridLevelState.OPEN_PENDING);
      expect(om.longLimits, name).toEqual([]);
    }
  });

  it("被动同步：同轮确认成交并立即挂平仓单，IDLE 层不挂新开仓单", async () => {
    const { manager, client, om } = makeManager();
    const pending = pendingLevel();
    const idle = new GridLevel({
      id: "L1", price: toDecimal("98"), amount: toDecimal("50"), side: "LONG",
      state: GridLevelState.IDLE,
    });
    manager.gridLevels.ETH = [pending, idle];
    client.openOrders = [];
    client.fills = [{ oid: 12345, px: "99.0", sz: "0.505", time: 1000 }];

    await manager.syncGridIncremental("ETH", false);
    // 同轮确认成交并立即挂平仓单：不让持仓在整个调度间隔里没有退出单保护
    expect(pending.state).toBe(GridLevelState.CLOSE_PENDING);
    expect(pending.openFillAmount!.eq("0.505")).toBe(true);
    expect(client.limitOrders.at(-1)!.ro).toBe(true);
    expect(idle.state).toBe(GridLevelState.IDLE); // 未挂新开仓单
    expect(om.longLimits).toEqual([]);
  });

  it("同一 oid 多笔部分成交必须聚合（量求和、价加权）", () => {
    const { manager } = makeManager();
    const level = pendingLevel();
    const fills = [
      { oid: 12345, px: "99.0", sz: "0.3", time: 1000 },
      { oid: 12345, px: "98.0", sz: "0.2", time: 1001 },
    ];
    expect(anyOf(manager).confirmFill(level, "open", fills)).toBe(true);
    expect(level.openFillAmount!.eq("0.5")).toBe(true);
    const expected = toDecimal("99.0").mul("0.3").plus(toDecimal("98.0").mul("0.2")).div("0.5");
    expect(level.openFillPrice!.eq(expected)).toBe(true);
  });
});

describe("手术式减仓", () => {
  // HL 拒单时外层仍是 status=ok，错误藏在内层 statuses：只判外层会把被拒当成功，
  // 层级被 reset 回收，实际仓位却还在——库存凭空「消失」。
  it("内层 error 保持原状不假关闭；成功才 reset 回收复用", async () => {
    for (const [result, want] of [[REJECTED_ORDER, GridLevelState.OPEN_FILLED], [FILLED_ORDER, GridLevelState.IDLE]] as const) {
      const { manager, client } = makeManager();
      const level = makeFilledLevel("L0", "LONG", "100", "1.0");
      manager.gridLevels.ETH = [level];
      (manager as never as { maxPositionNotionalUsd: number }).maxPositionNotionalUsd = 10.0;
      client.positions = [{ coin: "ETH", szi: "1.0" }];
      client.closeResults = [result];

      const reduced = await anyOf(manager).surgicalReduceAdverse("ETH", -1, 1.0);
      expect(reduced).toBe(want === GridLevelState.IDLE);
      expect(level.state).toBe(want);
    }
  });
});

describe("网格价位精度", () => {
  it("低价交易对不被拍扁到同一价位（历史 0.1 tick 硬编码缺陷），撞价则去重", async () => {
    const { manager, client } = makeManager();
    const prices = anyOf(manager).calculateGridPrices(0.10, 0.14, 5, "ARITHMETIC");
    const formatted = await anyOf(manager).formatGridPrices("DOGE", prices);
    expect(formatted.length).toBe(5);
    expect(formatted).toEqual([...new Set(formatted)].sort((a: number, b: number) => a - b));

    client.formatPrice = async (_s: string, price: number) => Math.round(price);
    expect(await anyOf(manager).formatGridPrices("ETH", [100.1, 100.2, 101.4])).toEqual([100, 101]);
  });
});

describe("幻影层级收尾（净额对冲）", () => {
  const closable = (side: "LONG" | "SHORT" = "LONG") => makeFilledLevel("L4", side, "100", "0.5");

  // 单向持仓下反向层级会被交易所净额对冲掉，平仓单因此被拒。
  // 只有「拒单文案匹配」且「该方向敞口确实已消失」双重确认，才允许收尾复用。
  it("双重确认成立：层级收尾复用且清空幻影库存", async () => {
    const cases: Array<[string, "LONG" | "SHORT", Dict[]]> = [
      ["LONG 层 + 无持仓", "LONG", []],
      ["SHORT 层 + 净持仓为多头", "SHORT", [{ coin: "ETH", szi: "0.5" }]],
    ];
    for (const [name, side, positions] of cases) {
      const { manager, client } = makeManager();
      const level = closable(side);
      client.positions = positions;
      client.limitOrderResults = [RO_NETTED_REJECTED_ORDER];
      await anyOf(manager).placeCloseOrder("ETH", level);
      expect(level.state, name).toBe(GridLevelState.IDLE);
      if (side === "LONG") expect(level.openFillPrice, name).toBeNull(); // 不再污染未实现盈亏
    }
  });

  it("三种不满足双重确认的情形：保留层级重试", async () => {
    const cases: Array<[string, Dict[] | null, Dict]> = [
      ["拒单文案匹配但同向持仓仍在", [{ coin: "ETH", szi: "0.5" }], RO_NETTED_REJECTED_ORDER],
      ["其他拒因（保证金）与净额对冲无关", [], REJECTED_ORDER],
      ["持仓查询失败（null）：未知状态不收尾", null, RO_NETTED_REJECTED_ORDER],
    ];
    for (const [name, positions, result] of cases) {
      const { manager, client } = makeManager();
      const level = closable("LONG");
      client.positions = positions;
      client.limitOrderResults = [result];
      await anyOf(manager).placeCloseOrder("ETH", level);
      expect(level.state, name).toBe(GridLevelState.OPEN_FILLED);
      expect(level.openFillPrice, name).not.toBeNull();
    }
  });
});

describe("紧急平仓 trades 落盘口径", () => {
  // 开启净额归因时，真实盈亏由 GRID_NET_CLOSE 记录；这里再写一次 pnl 就是双算。
  it("归因开启只在 reason 留痕不写 pnl；关闭时预估是唯一记录", async () => {
    for (const netting of [true, false]) {
      const client = new FakeGridClient();
      client.price = 90.0; // 产生非零未实现盈亏预估
      client.positions = [{ coin: "ETH", szi: "0.5" }];
      client.emergencyResults = [[true, FILLED_ORDER]];
      const { manager } = makeManager({ client, netting });
      const trades: Dict[] = [];
      const recorder = makeQuietLogger();
      (recorder as Dict).logTrade = (entry: Dict) => trades.push(entry);
      (manager as never as { logger: unknown }).logger = recorder;
      manager.gridLevels.ETH = [makeFilledLevel("L0", "LONG", "100", "0.5")];
      manager.pnlTrackers.ETH = new GridPnLTracker();

      expect(await manager.emergencyCloseAll("ETH", "TIME_LIMIT 测试")).toBe(true);
      const records = trades.filter((t) => t.action === "GRID_EMERGENCY_CLOSE");
      expect(records.length, String(netting)).toBe(1);
      if (netting) {
        expect(records[0].pnl ?? null).toBeNull();
        expect(String(records[0].reason)).toContain("预估盈亏");
        expect(String(records[0].reason)).toContain("TIME_LIMIT 测试");
      } else {
        expect(records[0].pnl).not.toBeNull();
        expect(records[0].reason).toBe("TIME_LIMIT 测试");
      }
    }
  });
});

// ── 重建闸门（冷却/突破/层数抖动） ──────────────────────────────────────

describe("重建闸门", () => {
  const newConfig = (overrides: Dict = {}): Dict => ({
    action: "UPDATE_GRID", lower_price: 95.0, upper_price: 105.0,
    grid_num: 6, amount_per_grid: 10.0, mode: "NEUTRAL", ...overrides,
  });
  function gatedManager(price: number | null = 100.0): GridManager {
    const client = new FakeGridClient();
    client.price = price;
    client.openOrders = [
      { oid: 801, coin: "ETH", side: "B", sz: "0.1", limitPx: "98.0" },
      { oid: 802, coin: "ETH", side: "A", sz: "0.1", limitPx: "102.0" },
    ];
    const { manager } = makeManager({ client });
    (manager.state.active_grids as Dict).ETH = {
      config: newConfig(),
      buy_orders: [{ oid: 801, px: 98.0 }],
      sell_orders: [{ oid: 802, px: 102.0 }],
    };
    return manager;
  }
  const setRebuildTs = (m: GridManager, ts: number) =>
    ((m as never as { lastRebuildTs: Record<string, number> }).lastRebuildTs.ETH = ts);

  // 冷却是抑制高频撤换单的主闸；只有价格真的突破旧区间（>0.5%）才提前解除。
  it("冷却期内不重建，唯有价格真突破旧区间才提前解除", async () => {
    const cases: Array<[string, number | null, Dict, boolean, string]> = [
      ["区间大改也挡住", 100.0, { lower_price: 80.0 }, false, "冷却"],
      ["价格突破上沿", 106.0, { upper_price: 110.0 }, true, "提前解除重建冷却"],
      ["仅贴近边界（<0.5%）不算突破", 105.4, { upper_price: 110.0 }, false, "冷却"],
      ["取价失败 fail-safe 偏向不重建", null, { lower_price: 80.0 }, false, "冷却"],
    ];
    for (const [name, price, cfg, wantRebuild, wantReason] of cases) {
      const manager = gatedManager(price);
      setRebuildTs(manager, Date.now() / 1000);
      const [should, reason] = await manager.shouldRebuildGrid("ETH", newConfig(cfg));
      expect(should, name).toBe(wantRebuild);
      expect(String(reason), name).toContain(wantReason);
    }
  });

  it("冷却过后：层数抖动不重建，方向变化照常重建", async () => {
    const cases: Array<[Dict, boolean, string]> = [
      [{ grid_num: 12 }, false, "层数变化=True"],
      [{ mode: "LONG" }, true, "类型/方向"],
    ];
    for (const [cfg, wantRebuild, wantReason] of cases) {
      const manager = gatedManager();
      setRebuildTs(manager, Date.now() / 1000 - 7200);
      const [should, reason] = await manager.shouldRebuildGrid("ETH", newConfig(cfg));
      expect(should, wantReason).toBe(wantRebuild);
      expect(String(reason)).toContain(wantReason);
    }
  });
});

// ── 开平仓闭环保全 ───────────────────────────────────────────────────────

describe("重建保全在途层级", () => {
  const closePendingLevel = (closeOid = 901): GridLevel => {
    const level = makeFilledLevel("L0", "LONG", "100", "0.5");
    level.state = GridLevelState.CLOSE_PENDING;
    level.closeOrderId = closeOid;
    return level;
  };

  it("在途层级连同平仓单跨重建保留", async () => {
    const client = new FakeGridClient();
    client.price = 100.0;
    // 901 是在途层级的 reduce_only 平仓单，802 是普通网格单
    client.openOrders = [
      { oid: 802, coin: "ETH", side: "A", sz: "0.1", limitPx: "102.0" },
      { oid: 901, coin: "ETH", side: "A", sz: "0.5", limitPx: "100.5" },
    ];
    client.fills = [];
    const { manager } = makeManager({ client });
    const carried = closePendingLevel();
    manager.gridLevels.ETH = [carried];
    (manager.state.active_grids as Dict).ETH = {
      config: { lower_price: 95.0, upper_price: 105.0, grid_num: 2 },
      buy_orders: [],
      sell_orders: [{ oid: 802, px: 102.0 }],
    };
    // 撤单后交易所只剩被保留的平仓单
    client.getOpenOrders = async () => client.openOrders!.filter((o) => o.oid === 901);

    await manager.syncGrid("ETH", {
      action: "UPDATE_GRID", lower_price: 90.0, upper_price: 110.0,
      grid_num: 2, amount_per_grid: 10.0, mode: "NEUTRAL",
    });

    expect(client.cancelCalls).not.toContain(901);
    const levels = manager.gridLevels.ETH;
    expect(levels).toContain(carried); // 必须并入新一代，不能被整体覆盖丢弃
    expect(carried.state).toBe(GridLevelState.CLOSE_PENDING);
    expect(carried.closeOrderId).toBe(901);
    expect(carried.id.startsWith("K")).toBe(true);
    expect(carried.openFillPrice!.eq("100")).toBe(true);
  }, 30000);

  it("账户级熔断走公共入口：绝不因在途层级白名单漏撤单", async () => {
    const client = new FakeGridClient();
    client.openOrders = [{ oid: 901, coin: "ETH", side: "A", sz: "0.5", limitPx: "100.5" }];
    const { manager } = makeManager({ client });
    manager.gridLevels.ETH = [closePendingLevel()];
    expect(await manager.cancelAllOrders("ETH")).toBe(true);
    expect(client.cancelCalls).toContain(901);
  });
});

describe("重建判定前先认领成交", () => {
  it("刚成交的层级被认领并同轮挂平仓单，而非被当挂单撤掉", async () => {
    const client = new FakeGridClient();
    client.price = 100.0;
    client.openOrders = []; // 开仓单已不在挂单列表 = 已成交
    client.fills = [{ oid: 12345, px: "99.0", sz: "0.505", time: 1000 }];
    const { manager } = makeManager({ client });

    const level = new GridLevel({
      id: "L0", price: toDecimal("99"), amount: toDecimal("50"), side: "LONG",
      state: GridLevelState.OPEN_PENDING,
    });
    level.openOrderId = 12345;
    manager.gridLevels.ETH = [level];
    (manager.state.active_grids as Dict).ETH = {
      config: { lower_price: 95.0, upper_price: 105.0, grid_num: 2 },
      buy_orders: [{ oid: 12345, px: 99.0 }],
      sell_orders: [],
    };
    (manager as never as { lastRebuildTs: Record<string, number> }).lastRebuildTs.ETH = Date.now() / 1000;

    await manager.syncGrid("ETH", {
      action: "UPDATE_GRID", lower_price: 95.0, upper_price: 105.0,
      grid_num: 2, amount_per_grid: 10.0, mode: "NEUTRAL",
    });

    expect(level.state).toBe(GridLevelState.CLOSE_PENDING);
    expect(level.openFillAmount!.eq("0.505")).toBe(true);
    expect(client.limitOrders.at(-1)!.ro).toBe(true);
  });

  it("KEEP_GRID 周期只被动同步一次（入口统一，不重复整轮）", async () => {
    const client = new FakeGridClient();
    client.price = 100.0;
    client.openOrders = [];
    const { manager } = makeManager({ client });
    manager.gridLevels.ETH = [makeFilledLevel()];

    const calls: boolean[] = [];
    const original = manager.syncGridIncremental.bind(manager);
    manager.syncGridIncremental = async (symbol: string, allowOpen = true) => {
      calls.push(allowOpen);
      return original(symbol, allowOpen);
    };
    await manager.syncGrid("ETH", { action: "KEEP_GRID" });
    expect(calls).toEqual([false]);
  });
});

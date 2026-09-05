/** 保护插件链测试：回撤熔断（含坏采样守卫）、单日亏损、连亏锁定与事件分发。 */
import { describe, expect, it } from "vitest";
import path from "node:path";
import { ProtectionAction, ProtectionManager, protectionReturn, type ProtectionContext } from "../src/plugins/protections/index.js";
import { makeTempDir } from "./support.js";

const silent = { info() {}, warn() {}, error() {} };

function makeContext(equity: number, timestamp?: number): ProtectionContext {
  return {
    balance: equity,
    equity,
    unrealizedPnl: 0,
    marginUsed: 0,
    currentPositions: [],
    timestamp: timestamp ?? Date.now(),
  };
}

function makeManager(config: Record<string, unknown>[], onTriggered?: (r: string) => void): ProtectionManager {
  return new ProtectionManager({
    protectionsConfig: config,
    dataDir: path.join(makeTempDir(), "protection"),
    logger: silent,
    onProtectionTriggered: onTriggered,
  });
}

const DRAWDOWN = [{ name: "max_drawdown", max_drawdown_pct: 0.10 }];

describe("净值类保护", () => {
  it.each([
    { name: "回撤 15% 达阈值", equity: 850, expected: ProtectionAction.CLOSE_ALL_POSITIONS },
    { name: "回撤 5% 未达阈值", equity: 950, expected: ProtectionAction.NONE },
  ])("最大回撤：$name", ({ equity, expected }) => {
    const manager = makeManager(DRAWDOWN);
    expect(manager.checkAll(makeContext(1000))).toEqual([]); // 建立峰值
    expect(ProtectionManager.getMostSevereAction(manager.checkAll(makeContext(equity)))).toBe(expected);
  });

  it("单日亏损达阈值暂停开仓", () => {
    const manager = makeManager([{ name: "daily_loss", max_daily_loss_pct: 0.05 }]);
    manager.checkAll(makeContext(1000)); // 建立当日起点
    const results = manager.checkAll(makeContext(940)); // 当日 -6%
    expect(ProtectionManager.getMostSevereAction(results)).toBe(ProtectionAction.PAUSE_NEW_TRADES);
  });
});

describe("回撤坏采样守卫", () => {
  // 统一账户接口降级时净值会瞬间读成一个假的小数字，直接熔断=无故清仓
  it("单次骤降逾 50% 只告警不熔断；净值恢复即清除疑点", () => {
    const manager = makeManager(DRAWDOWN);
    expect(manager.checkAll(makeContext(1000))).toEqual([]); // 峰值 1000
    expect(manager.checkAll(makeContext(200))).toEqual([]); // 骤降 80%：等待确认
    expect(manager.checkAll(makeContext(995))).toEqual([]); // 恢复，虚惊一场
  });

  it("连续两周期骤降=确认真实回撤；未过 50% 的正常回撤即时触发", () => {
    const confirmed = makeManager(DRAWDOWN);
    confirmed.checkAll(makeContext(1000));
    confirmed.checkAll(makeContext(200));
    expect(confirmed.checkAll(makeContext(210))[0]?.action).toBe(ProtectionAction.CLOSE_ALL_POSITIONS);

    const normal = makeManager(DRAWDOWN);
    normal.checkAll(makeContext(1000));
    expect(normal.checkAll(makeContext(850))[0]?.action).toBe(ProtectionAction.CLOSE_ALL_POSITIONS);
  });
});

describe("连续亏损", () => {
  const CONFIG = [{ name: "consecutive_loss", max_consecutive_losses: 3, per_symbol: true, pause_hours: 4 }];

  it("per-symbol 连亏达阈值锁定，其他交易对不受影响，锁定到期自动解除", () => {
    const manager = makeManager(CONFIG);
    for (let i = 0; i < 3; i++) manager.onTradeClose({ symbol: "BTC", pnl: -5 });
    const [locked, reason] = manager.isSymbolLocked("BTC");
    expect(locked).toBe(true);
    expect(reason.includes("BTC") || reason.includes("连续亏损")).toBe(true);
    expect(manager.isSymbolLocked("ETH")[0]).toBe(false);
    expect(manager.isSymbolLocked("BTC", Date.now() + 5 * 3_600_000)[0]).toBe(false);
  });

  it.each([
    // 真盈利了结才算打破连亏
    { name: "真盈利重置计数", pnl: 8, forced: false, locked: false },
    // forced_close_no_reset：风控强平的浮盈不是策略赢了，不得清零
    { name: "强平浮盈不算打破连亏", pnl: 3, forced: true, locked: true },
  ])("计数重置规则：$name", ({ pnl, forced, locked }) => {
    const manager = makeManager([{ ...CONFIG[0], forced_close_no_reset: true }]);
    manager.onTradeClose({ symbol: "BTC", pnl: -5 });
    manager.onTradeClose({ symbol: "BTC", pnl: -5 });
    manager.onTradeClose({ symbol: "BTC", pnl, forced });
    manager.onTradeClose({ symbol: "BTC", pnl: -5 });
    expect(manager.isSymbolLocked("BTC")[0]).toBe(locked);
  });

  it("暂停到期必须清零计数（否则空仓时每个冷却期重复触发=永久锁死）", () => {
    const manager = makeManager([
      { name: "consecutive_loss", max_consecutive_losses: 2, per_symbol: false, pause_hours: 1 },
    ]);
    const t0 = Date.parse("2026-08-05T10:00:00Z");
    manager.onTradeClose({ symbol: "BTC", pnl: -1, timestamp: t0 });
    manager.onTradeClose({ symbol: "BTC", pnl: -1, timestamp: t0 });
    const results = manager.checkAll(makeContext(1000, t0));
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].action).toBe(ProtectionAction.PAUSE_NEW_TRADES);

    // 暂停期后（无任何平仓事件）：必须恢复且不再重复触发
    const t1 = t0 + 61 * 60_000;
    expect(manager.checkAll(makeContext(1000, t1))).toEqual([]);
    expect(manager.checkAll(makeContext(1000, t1 + 5 * 60_000))).toEqual([]);
  });
});

describe("Manager 行为", () => {
  it.each([
    { name: "未知插件忽略", config: [{ name: "not_exists" }] },
    { name: "禁用插件忽略", config: [{ name: "max_drawdown", enabled: false }] },
  ])("装载：$name", ({ config }) => {
    expect(makeManager(config).plugins).toEqual([]);
  });

  it("按最严重动作聚合；同一暂停期内触发回调只发一次", () => {
    expect(ProtectionManager.getMostSevereAction([
      protectionReturn({ triggered: true, action: ProtectionAction.PAUSE_NEW_TRADES }),
      protectionReturn({ triggered: true, action: ProtectionAction.CLOSE_ALL_POSITIONS }),
    ])).toBe(ProtectionAction.CLOSE_ALL_POSITIONS);
    expect(ProtectionManager.getMostSevereAction([])).toBe(ProtectionAction.NONE);

    const triggered: string[] = [];
    const manager = makeManager(DRAWDOWN, (r) => triggered.push(r));
    manager.checkAll(makeContext(1000));
    manager.checkAll(makeContext(800));
    manager.checkAll(makeContext(800));
    expect(triggered.length).toBe(1);
    expect(triggered[0]).toContain("回撤");
  });
});

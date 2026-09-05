/** 网格策略编排测试：趋势过滤、Triple Barrier 短路、空转自愈与 LLM 健康跟踪。 */
import { describe, expect, it } from "vitest";
import { GridStrategy } from "../src/strategy/grid.js";
import { ConfigSchema, type GridSection, type QuantFlowConfigInput } from "../src/config.js";
import { ProtectionAction, protectionReturn } from "../src/plugins/protections/index.js";
import { FakeOrderManager, makeOhlcv, makeQuietLogger } from "./support.js";
import type { Dict } from "../src/trading/client.js";

/** GridManager 桩：记录 sync/flatten 调用，行为可配置。 */
class StubGridManager {
  synced: Dict[] = [];
  flattenCalls: number[] = [];
  barrierTriggered = false;
  idle = true;
  barrierChecks = 0;
  maintainCalls = 0;
  emergencyCloseCalls: string[] = [];
  emergencyCloseOk = true;
  retryPendingCalls = 0;

  async reconcileNettingCloses(_s: string): Promise<Dict> {
    return { processed: 0 };
  }
  async retryPendingEmergencyClose(_s: string): Promise<void> {
    this.retryPendingCalls += 1;
  }
  async checkBarrier(_s: string): Promise<boolean> {
    this.barrierChecks += 1;
    return this.barrierTriggered;
  }
  async getGridSummary(_s: string): Promise<string> {
    return "无网格";
  }
  async syncGrid(_s: string, decision: Dict): Promise<void> {
    this.synced.push(decision);
  }
  async flattenAdverseInventory(_s: string, trendDir: number): Promise<boolean> {
    this.flattenCalls.push(trendDir);
    return true;
  }
  async cancelAllOrders(_s: string): Promise<boolean> {
    return true;
  }
  async emergencyCloseSymbol(_s: string, reason: string): Promise<boolean> {
    this.emergencyCloseCalls.push(reason);
    return this.emergencyCloseOk;
  }
  async maintainProtectiveOrders(_s: string): Promise<void> {
    this.maintainCalls += 1;
  }
  async isGridIdle(_s: string): Promise<boolean> {
    return this.idle;
  }
}

/** GridAgent 桩：返回预置决策序列。 */
class StubGridAgent {
  fallbackCalls = 0;
  fallback: Dict;
  constructor(
    public decisions: Dict[],
    fallback?: Dict,
  ) {
    this.fallback = fallback ?? {
      action: "UPDATE_GRID", mode: "NEUTRAL", confidence: 0, reason: "兜底重建", llm_ok: false, fallback: true,
    };
  }
  async makeDecision(_m: Dict, _t: Dict, _s: string): Promise<Dict> {
    return this.decisions.length > 1 ? this.decisions.shift()! : this.decisions[0];
  }
  async buildFallbackConfig(_m: Dict): Promise<Dict> {
    this.fallbackCalls += 1;
    return { ...this.fallback };
  }
}

class StubFetcher {
  async fetchOhlcv(): Promise<ReturnType<typeof makeOhlcv>> {
    return makeOhlcv();
  }
}

const DEGRADED_KEEP: Dict = {
  action: "KEEP_GRID", mode: "NEUTRAL", confidence: 0, reason: "LLM 故障兜底", llm_ok: false,
};

function gridConfig(overrides: Partial<GridSection> = {}): GridSection {
  const validated = new (ConfigSchema as never as new (v: unknown) => QuantFlowConfigInput)({});
  return { ...validated.grid, trend_filter_enabled: false, ...overrides };
}

function makeStrategy(options: {
  agent: StubGridAgent;
  manager?: StubGridManager;
  config?: GridSection;
  pm?: Dict | null;
  positions?: Dict[];
  available?: number;
}): { strategy: GridStrategy; manager: StubGridManager; om: FakeOrderManager } {
  const manager = options.manager ?? new StubGridManager();
  const om = new FakeOrderManager(options.available ?? 500.0, options.positions ?? []);
  const strategy = new GridStrategy({
    symbol: "ETH",
    gridAgent: options.agent as never,
    gridManager: manager as never,
    orderManager: om as never,
    marketFetcher: new StubFetcher() as never,
    logger: makeQuietLogger(),
    gridConfig: options.config ?? gridConfig(),
    timeframe: "1h",
    protectionManager: (options.pm ?? null) as never,
  });
  return { strategy, manager, om };
}


describe("周期短路与自愈", () => {
  it("屏障触发后本轮不布单", async () => {
    const manager = new StubGridManager();
    manager.barrierTriggered = true;
    const { strategy } = makeStrategy({ agent: new StubGridAgent([DEGRADED_KEEP]), manager });
    await strategy.runCycle();
    expect(manager.synced).toEqual([]);
  });

  it("净值低于停机线且无持仓：整轮短路", async () => {
    const agent = new StubGridAgent([{ action: "KEEP_GRID", llm_ok: true, reason: "不该被调用" }]);
    // FakeOrderManager 净值 500 < 停机线 1000 且无持仓
    const { strategy, manager } = makeStrategy({ agent, config: gridConfig({ halt_below_usd: 1000.0 }) });
    await strategy.runCycle();
    expect(manager.synced).toEqual([]);
  });

  it("LLM 连续故障导致空转达阈值后，不经 LLM 兜底重建", async () => {
    const agent = new StubGridAgent([DEGRADED_KEEP]);
    const { strategy, manager } = makeStrategy({ agent, config: gridConfig({ llm_fallback_rebuild_cycles: 2 }) });
    await strategy.runCycle();
    expect(manager.synced.at(-1)!.action).toBe("KEEP_GRID");
    expect(agent.fallbackCalls).toBe(0);
    await strategy.runCycle();
    expect(agent.fallbackCalls).toBe(1);
    expect(manager.synced.at(-1)!.action).toBe("UPDATE_GRID");
    expect(manager.synced.at(-1)!.fallback).toBe(true);
  });

  // 兜底重建会撤换全部挂单，误触发的代价很高：只有「LLM 坏了且网格真的空着」才允许。
  it("三种情形绝不触发兜底：网格非空转 / 开关关闭 / LLM 健康时主动 KEEP_GRID", async () => {
    const cases: Array<[string, StubGridAgent, StubGridManager, Partial<GridSection>]> = [
      ["交易所仍有活跃网格", new StubGridAgent([DEGRADED_KEEP]), Object.assign(new StubGridManager(), { idle: false }), { llm_fallback_rebuild_cycles: 1 }],
      ["cycles=0 关闭兜底", new StubGridAgent([DEGRADED_KEEP]), new StubGridManager(), { llm_fallback_rebuild_cycles: 0 }],
      ["LLM 健康时的 KEEP_GRID 是 AI 的明确决策", new StubGridAgent([{ action: "KEEP_GRID", llm_ok: true, reason: "AI 主动维持" }]), new StubGridManager(), { llm_fallback_rebuild_cycles: 1 }],
    ];
    for (const [name, agent, manager, cfg] of cases) {
      const { strategy } = makeStrategy({ agent, manager, config: gridConfig(cfg) });
      for (let i = 0; i < 3; i++) await strategy.runCycle();
      expect(agent.fallbackCalls, name).toBe(0);
    }
  });

  it("LLM 连败计数达阈值告警，恢复后复位", async () => {
    const agent = new StubGridAgent([DEGRADED_KEEP]);
    const { strategy } = makeStrategy({ agent, config: gridConfig({ llm_failure_alert_cycles: 2 }) });
    await strategy.runCycle();
    await strategy.runCycle();
    expect(strategy.health.llm_failure_streak).toBe(2);
    expect(strategy.health.llm_alert_sent).toBe(true);
    agent.decisions = [{ action: "KEEP_GRID", llm_ok: true, reason: "恢复" }];
    await strategy.runCycle();
    expect(strategy.health.llm_failure_streak).toBe(0);
    expect(strategy.health.llm_alert_sent).toBe(false);
  });
});

describe("趋势过滤与形态闸门", () => {
  // makeOhlcv 是线性上行序列：所有周期 analyzeTrend=强势上涨，min_votes=1 必触发
  const strongTrendConfig = (overrides: Partial<GridSection> = {}) =>
    gridConfig({
      trend_filter_enabled: true,
      trend_filter_min_votes: 1,
      trend_confirm_cycles: 1,
      flatten_min_cycles: 2,
      flatten_adverse: true,
      ...overrides,
    });

  it("强趋势只挂顺势侧（默认）：仍调 LLM，逆势侧由 allowed_open_side 关掉", async () => {
    const agent = new StubGridAgent([{ action: "UPDATE_GRID", llm_ok: true, reason: "顺势布单" }]);
    const { strategy, manager } = makeStrategy({ agent, config: strongTrendConfig() });
    await strategy.runCycle();
    const decision = manager.synced.at(-1)!;
    expect(decision.action).toBe("UPDATE_GRID");
    expect(decision.allowed_open_side).toBe("buy");
    expect(decision.trend_side).toBe(1);
    expect(decision.trend_paused).toBeUndefined();
  });

  // 强趋势全面暂停。同时验证「暂停先行、平仓靠后」的两级确认，
  // 以及主动暂停不得被计成空转（否则会触发兜底重建，在趋势里重新布满单）。
  it("trend_side_only=false：全面暂停且不调 LLM，平逆势库存需更多确认，且不算空转", async () => {
    const agent = new StubGridAgent([{ action: "UPDATE_GRID", llm_ok: true, reason: "不该被调用" }]);
    const { strategy, manager } = makeStrategy({
      agent,
      config: strongTrendConfig({ trend_side_only: false, llm_fallback_rebuild_cycles: 1 }),
    });
    await strategy.runCycle();
    const decision = manager.synced.at(-1)!;
    expect(decision.action).toBe("KEEP_GRID");
    expect(decision.trend_paused).toBe(true);
    expect(decision.llm_ok).toBe(true); // 未调 LLM，不计入故障
    expect(decision.allowed_open_side).toBeUndefined();
    expect(manager.flattenCalls).toEqual([]); // 平仓需 2 周期确认

    await strategy.runCycle();
    expect(manager.flattenCalls).toEqual([1]);
    expect(agent.fallbackCalls).toBe(0); // 主动暂停不算空转
  });

  it("形态闸门：效率比过高时不开新仓（allowed_open_side=none），退出通道照常", async () => {
    const agent = new StubGridAgent([{ action: "UPDATE_GRID", llm_ok: true, reason: "不该被调用" }]);
    // makeOhlcv 是严格线性上行 → 效率比 = 1，必然越过任何阈值
    const { strategy, manager } = makeStrategy({
      agent,
      config: gridConfig({ range_filter_er_max: 0.4, range_filter_lookback: 50 }),
    });
    await strategy.runCycle();
    const decision = manager.synced.at(-1)!;
    expect(decision.action).toBe("KEEP_GRID");
    expect(decision.allowed_open_side).toBe("none");
    expect(decision.efficiency_ratio).toBeCloseTo(1, 6);
    expect(decision.trend_paused).toBe(true); // 主动停手不计入 LLM 故障与空转
    expect(strategy.health.range_gate_streak).toBe(1);
  });
});

/** 保护链桩：动作可配置，记录 onPositionDropped 调用。 */
class StubProtectionManager {
  dropped: string[] = [];
  lockedSymbols = new Set<string>();
  constructor(public action: ProtectionAction) {}
  checkAll(_context: Dict) {
    if (this.action === ProtectionAction.NONE) return [];
    return [protectionReturn({ triggered: true, action: this.action, reason: "桩触发" })];
  }
  onPositionDropped(symbol: string): void {
    this.dropped.push(symbol);
  }
  isSymbolLocked(symbol: string): [boolean, string] {
    return this.lockedSymbols.has(symbol) ? [true, "连亏锁定桩"] : [false, ""];
  }
}

describe("账户级保护与顺序（历史缺陷回归）", () => {
  // 历史缺陷：PAUSE 直接 return，连带跳过屏障与保护单维护，暂停期内亏损不封底。
  it("PAUSE 只暂停新开仓：Barrier 照查、保护单照维护、待重试强平照重试、不调 LLM/布单", async () => {
    const pm = new StubProtectionManager(ProtectionAction.PAUSE_NEW_TRADES);
    const agent = new StubGridAgent([{ action: "UPDATE_GRID", llm_ok: true, reason: "不该被调用" }]);
    const { strategy, manager } = makeStrategy({ agent, pm });
    await strategy.runCycle();
    await strategy.runCycle();
    expect(manager.barrierChecks).toBe(2);
    expect(manager.maintainCalls).toBe(2);
    expect(manager.retryPendingCalls).toBe(2);
    expect(manager.synced).toEqual([]);
  });

  it("暂停期内屏障触发优先，本轮不再维护保护单", async () => {
    const pm = new StubProtectionManager(ProtectionAction.PAUSE_NEW_TRADES);
    const manager = new StubGridManager();
    manager.barrierTriggered = true;
    const { strategy } = makeStrategy({ agent: new StubGridAgent([DEGRADED_KEEP]), manager, pm });
    await strategy.runCycle();
    expect(manager.barrierChecks).toBe(1);
    expect(manager.maintainCalls).toBe(0);
    expect(manager.synced).toEqual([]);
  });

  // 只有确认平仓成功才清保护记录：失败还清，超时/回撤保护会永远失明于平不掉的仓位。
  it("CLOSE_ALL：走 emergencyCloseSymbol，成功才 onPositionDropped，失败绝不清记录", async () => {
    for (const ok of [true, false]) {
      const pm = new StubProtectionManager(ProtectionAction.CLOSE_ALL_POSITIONS);
      const manager = new StubGridManager();
      manager.emergencyCloseOk = ok;
      const { strategy } = makeStrategy({
        agent: new StubGridAgent([DEGRADED_KEEP]), manager, pm,
        positions: [{ coin: "ETH", szi: "0.5", entryPx: "100" }],
      });
      await strategy.runCycle();
      expect(manager.emergencyCloseCalls, String(ok)).toEqual(["账户熔断强平"]);
      expect(pm.dropped, String(ok)).toEqual(ok ? ["ETH"] : []);
      expect(manager.synced, String(ok)).toEqual([]);
    }
  });

  it("连亏锁定期内 UPDATE_GRID 降级 KEEP_GRID（网格路径消费锁）", async () => {
    const pm = new StubProtectionManager(ProtectionAction.NONE);
    pm.lockedSymbols.add("ETH");
    const agent = new StubGridAgent([{ action: "UPDATE_GRID", llm_ok: true, confidence: 0.9, reason: "扩建" }]);
    const { strategy, manager } = makeStrategy({ agent, pm });
    await strategy.runCycle();
    expect(manager.synced.at(-1)!.action).toBe("KEEP_GRID");
    expect(String(manager.synced.at(-1)!.reason)).toContain("锁定");
  });
});

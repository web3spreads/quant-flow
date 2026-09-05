/** 网格决策 Agent 测试：动作白名单、故障兜底与数学引擎衔接。 */
import { describe, expect, it } from "vitest";
import { GridAgent } from "../src/strategy/gridAgent.js";
import { LLMError } from "../src/llm.js";
import { FakeOrderManager, QUIET_LOGGER, makeFakeLLM } from "./support.js";
import type { Dict } from "../src/trading/client.js";

const MARKET_DATA = {
  current_price: 100.0, rsi: 55.0, macd_hist: 0.1,
  bb_upper: 104.0, bb_lower: 96.0, high: 101.0, low: 99.0, volume_change: 5.0,
};
const TRENDS = { "1小时": "震荡整理" };

function makeAgent(
  llmSpec: { replies?: string[]; error?: Error },
  om?: FakeOrderManager,
  overrides: Dict = {},
): { agent: GridAgent; backend: ReturnType<typeof makeFakeLLM>["backend"] } {
  const { llm, backend } = makeFakeLLM(llmSpec.replies ?? [], llmSpec.error ?? null);
  const agent = new GridAgent({
    symbol: "ETH",
    orderManager: (om ?? new FakeOrderManager(1000.0)) as never,
    logger: QUIET_LOGGER,
    llm,
    tradeAmount: 100.0,
    forceNeutralMode: false,
    adaptiveSizing: true,
    ...overrides,
  });
  return { agent, backend };
}

const gridJson = (kwargs: Dict = {}) =>
  JSON.stringify({ action: "KEEP_GRID", mode: "NEUTRAL", confidence: 0.7, reason: "测试", ...kwargs });

const decide = (agent: GridAgent) => agent.makeDecision(MARKET_DATA, TRENDS, "无网格");

describe("故障降级为 KEEP_GRID", () => {
  const cases: Array<{
    name: string;
    spec: { replies?: string[]; error?: Error };
    om?: () => FakeOrderManager;
    llmOk: boolean;
  }> = [
    { name: "LLM 调用异常", spec: { error: new LLMError("模型下线") }, llmOk: false },
    { name: "回复不可解析", spec: { replies: ["不是 JSON 的回复"] }, llmOk: false },
    { name: "非法 action（线上真实出现过 UPDATE_GRIDLE）", spec: { replies: [gridJson({ action: "UPDATE_GRIDLE" })] }, llmOk: false },
    // 余额接口故障的锅不在 LLM：误标 llm_ok=false 会污染 LLM 连败告警
    {
      name: "余额接口故障（llm_ok 必须为 true）",
      spec: { replies: [gridJson({ action: "UPDATE_GRID" })] },
      om: () => {
        const om = new FakeOrderManager();
        om.balanceOk = false;
        return om;
      },
      llmOk: true,
    },
    { name: "LLM 正常返回 KEEP_GRID（直通）", spec: { replies: [gridJson()] }, llmOk: true },
  ];

  it.each(cases)("$name → KEEP_GRID，llm_ok=$llmOk", async ({ spec, om, llmOk }) => {
    const { agent } = makeAgent(spec, om?.());
    const decision = await decide(agent);
    expect(decision.action).toBe("KEEP_GRID");
    expect(decision.llm_ok).toBe(llmOk);
  });
});

describe("UPDATE_GRID", () => {
  it("产出数学引擎配置", async () => {
    const { agent } = makeAgent({ replies: [gridJson({ action: "UPDATE_GRID", width_pct: 0.06, grid_num: 6 })] });
    const decision = await decide(agent);
    expect(decision.action).toBe("UPDATE_GRID");
    expect(Number(decision.lower_price)).toBeLessThan(100);
    expect(Number(decision.upper_price)).toBeGreaterThan(100);
    expect(decision.llm_ok).toBe(true);
    expect(decision.confidence).toBe(0.7);
  });
  it("强制中性覆盖 AI 方向", async () => {
    const { agent } = makeAgent(
      { replies: [gridJson({ action: "UPDATE_GRID", mode: "LONG" })] },
      undefined,
      { forceNeutralMode: true },
    );
    expect((await decide(agent)).mode).toBe("NEUTRAL");
  });
  it("资金不足拒绝布单", async () => {
    const om = new FakeOrderManager(7.71);
    const { agent } = makeAgent({ replies: [gridJson({ action: "UPDATE_GRID" })] }, om, { maxLeverage: 5 });
    const decision = await decide(agent);
    expect(decision.action).toBe("INSUFFICIENT_CAPITAL");
    expect(decision.llm_ok).toBe(true);
  });
});

describe("兜底重建", () => {
  it("不经 LLM 产出中性网格；无当前价则保持网格", async () => {
    const { agent, backend } = makeAgent({ error: new LLMError("不该被调用") });
    const config = await agent.buildFallbackConfig(MARKET_DATA);
    expect(config.action).toBe("UPDATE_GRID");
    expect(config.mode).toBe("NEUTRAL");
    expect(config.fallback).toBe(true);
    expect(config.llm_ok).toBe(false);

    expect((await agent.buildFallbackConfig({ current_price: 0 })).action).toBe("KEEP_GRID");
    expect(backend.calls).toEqual([]); // 全程未调用 LLM
  });
});

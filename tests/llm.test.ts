/** LLM 客户端测试：JSON 提取、重试语义、每日预算闸与规则后端。 */
import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { LLMBudgetError, LLMClient, LLMError, LlmUsageTracker, RuleGridLlmBackend, extractJson, type LLMBackend } from "../src/llm.js";
import { clock } from "../src/utils/clock.js";
import { makeTempDir } from "./support.js";

describe("extractJson", () => {
  it("各种回复形态恒定提取出对象，提不出则抛错（绝不返回数组/空值）", () => {
    // 返回值必须恒为对象：数组透传到策略层会属性访问崩溃，绕过 llm_ok=false 的降级契约。
    const ok: Array<[name: string, input: unknown, expected: unknown]> = [
      ["裸 JSON", '{"action": "HOLD"}', { action: "HOLD" }],
      ["围栏 JSON", '前置说明\n```json\n{"action": "BUY", "confidence": 0.8}\n```\n后置', { action: "BUY", confidence: 0.8 }],
      [
        "文本中嵌 JSON（字符串含花括号）",
        '我认为应该观望。{"action": "HOLD", "reason": "含{括号}的字符串"}完',
        { action: "HOLD", reason: "含{括号}的字符串" },
      ],
      ["数组内含对象时提取该对象", '[{"action": "HOLD"}]', { action: "HOLD" }],
    ];
    for (const [name, input, expected] of ok) {
      expect(extractJson(input), name).toEqual(expected);
    }

    const bad: Array<[name: string, input: string]> = [
      ["空输入", ""],
      ["无 JSON 的纯文本", "纯文本，没有任何对象"],
      ["顶层数组", '["BUY", "SELL"]'],
    ];
    for (const [name, input] of bad) {
      expect(() => extractJson(input), name).toThrow();
    }
  });
});

/** 可编排后端：按序返回内容或抛异常。 */
class SeqBackend implements LLMBackend {
  calls: Array<{ system: string; user: string; temperature: number }> = [];
  constructor(private readonly steps: Array<string | Error>) {}
  describe() {
    return "seq";
  }
  async chatOnce(system: string, user: string, temperature: number): Promise<string> {
    this.calls.push({ system, user, temperature });
    const step = this.steps.shift();
    if (step === undefined) throw new Error("步骤耗尽");
    if (step instanceof Error) throw step;
    return step;
  }
}

function makeClient(steps: Array<string | Error>): { client: LLMClient; backend: SeqBackend } {
  const backend = new SeqBackend(steps);
  return { client: new LLMClient({ backend, model: "m", maxRetries: 3, backoffScale: 0 }), backend };
}

describe("LLMClient.chat", () => {
  it("成功返回内容", async () => {
    const { client } = makeClient(["回复内容"]);
    await expect(client.chat("sys", "user")).resolves.toBe("回复内容");
  });

  it("空回复计入重试", async () => {
    const { client } = makeClient(["", "第二次成功"]);
    await expect(client.chat("sys", "user")).resolves.toBe("第二次成功");
  });

  it("网络错误重试后恢复", async () => {
    const { client, backend } = makeClient([new Error("网络抖动"), new Error("网络抖动"), "恢复"]);
    await expect(client.chat("sys", "user")).resolves.toBe("恢复");
    expect(backend.calls.length).toBe(3);
  });

  it("规则后端：零外部请求、输出合法 JSON、每次调用计数", async () => {
    const backend = new RuleGridLlmBackend();
    const client = new LLMClient({ backend, model: "rule", maxRetries: 1, backoffScale: 0 });
    const parsed = extractJson(await client.chat("sys", "user"));
    expect(parsed.action).toBe("UPDATE_GRID");
    expect(parsed.mode).toBe("NEUTRAL");
    expect(backend.calls).toBe(1);
    expect(client.usage).toBeNull(); // 规则后端不接预算闸
    expect(client.describe()).toMatch(/rule-grid/);
  });

  it("重试耗尽抛 LLMError，且错误里带得出是哪个后端", async () => {
    // provider/model 名不被适配器识别时，流会正常结束但零分片，症状与限流、
    // 余额不足、网络抖动完全相同。错误信息不带后端标识就只能看到「空回复」，
    // 定位方向会被带偏到密钥/网络上去。
    await expect(makeClient(["", "", ""]).client.chat("sys", "user")).rejects.toThrow(/seq/);
    const fail = () => new Error("持续故障");
    const attempt = makeClient([fail(), fail(), fail()]).client.chat("sys", "user");
    await expect(attempt).rejects.toThrowError(LLMError);
    await expect(attempt).rejects.toThrow(/持续故障/);
  });
});

describe("每日预算闸（LlmUsageTracker）", () => {
  // 2026-09 的事故：批处理靠重试放大把共享 key 打穿。预算必须按「每一次实际请求」计，
  // 而且每次计数都落盘——进程重启不能把计数清零。
  const restore: Array<() => void> = [];
  afterEach(() => {
    while (restore.length) restore.pop()!();
  });
  const freezeAt = (iso: string) => restore.push(clock.install(() => Date.parse(iso)));

  it("重试每次都计数；触顶抛 LLMBudgetError（是 LLMError 子类）且不再打后端；跨日归零", async () => {
    freezeAt("2026-09-05T10:00:00Z");
    const file = path.join(makeTempDir(), "llm-usage.json");
    const usage = new LlmUsageTracker({ file, cap: 3 });
    const backend = new SeqBackend([new Error("抖动"), new Error("抖动"), "ok", "不该到这里"]);
    const client = new LLMClient({ backend, model: "m", maxRetries: 3, backoffScale: 0, usage });

    // 一次 chat 经两次失败重试成功：消耗 3 次额度
    await expect(client.chat("s", "u")).resolves.toBe("ok");
    expect(backend.calls.length).toBe(3);
    expect(usage.snapshot()).toMatchObject({ date: "2026-09-05", calls: 3, cap: 3, capped: true });

    // 触顶：不再调后端，抛预算错误
    const attempt = client.chat("s", "u");
    await expect(attempt).rejects.toBeInstanceOf(LLMBudgetError);
    await expect(attempt).rejects.toBeInstanceOf(LLMError);
    expect(backend.calls.length).toBe(3);
    // 当天只告警一次
    expect(usage.markCappedWarned()).toBe(true);
    expect(usage.markCappedWarned()).toBe(false);

    // 计数已落盘：新进程读回同一天的数字，仍然触顶
    const reloaded = new LlmUsageTracker({ file, cap: 3 });
    expect(reloaded.snapshot()).toMatchObject({ calls: 3, capped: true });
    expect(reloaded.markCappedWarned()).toBe(false);

    // 跨日归零
    freezeAt("2026-09-06T00:00:01Z");
    expect(reloaded.snapshot()).toMatchObject({ date: "2026-09-06", calls: 0, capped: false });
    expect(reloaded.tryConsume()).toBe(true);
    expect(JSON.parse(fs.readFileSync(file, "utf-8"))).toMatchObject({ date: "2026-09-06", calls: 1 });
  });

  it("计数文件损坏：当天从零起算并告警，不拖垮启动", () => {
    freezeAt("2026-09-05T10:00:00Z");
    const file = path.join(makeTempDir(), "llm-usage.json");
    fs.writeFileSync(file, "{not json");
    const warnings: string[] = [];
    const usage = new LlmUsageTracker({ file, cap: 5, warn: (m) => warnings.push(m) });
    expect(usage.snapshot().calls).toBe(0);
    expect(warnings.join("\n")).toMatch(/llm-usage\.json/);
  });
});

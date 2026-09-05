/** LLM 客户端测试：JSON 提取与重试语义。 */
import { describe, expect, it } from "vitest";
import { LLMClient, LLMError, extractJson, type LLMBackend } from "../src/llm.js";

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

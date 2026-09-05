/**
 * LLM 客户端：双后端。
 *
 * - "openai"：任意 OpenAI 兼容端点（DeepSeek/OpenAI/本地部署/网关）。
 *   有界指数退避重试、空回复计入重试、重试耗尽抛 LLMError。
 * - "dsh"：寄生宿主 DeepSeek Harness 的 `llm` 服务（ctx.llm.stream()），由 dsh
 *   统一管理供应商/密钥/计量。服务缺失或流式失败同样归一为 LLMError——
 *   调用方的原则不变：「LLM 故障绝不放大成交易动作」。
 *
 * 空回复视为故障并计入重试——推理类模型偶发返回「仅含 reasoning、正文为空」
 * 的回复，重发即可绕开（线上实测有效）。
 */

import { sleep } from "./utils/sleep.js";

/** 瞬时故障的重试等待上限（秒） */
const MAX_BACKOFF_SECONDS = 5.0;

/** LLM 调用失败（重试耗尽后抛出）。 */
export class LLMError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LLMError";
  }
}

export interface LLMBackend {
  /** 单次调用：返回助手回复文本；失败抛任意异常（由 LLMClient 统一重试）。 */
  chatOnce(system: string, user: string, temperature: number): Promise<string>;
  /** 展示用描述（看板/日志） */
  describe(): string;
}


/** OpenAI 兼容后端：直接 fetch POST /chat/completions。 */
export class OpenAICompatBackend implements LLMBackend {
  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    readonly model: string,
    private readonly timeoutSecs: number,
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  describe(): string {
    return `openai-compatible ${this.model} @ ${this.baseUrl}`;
  }

  async chatOnce(system: string, user: string, temperature: number): Promise<string> {
    const resp = await fetch(`${this.baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: this.model,
        messages: [
          { role: "system", content: system },
          { role: "user", content: user },
        ],
        temperature,
      }),
      signal: AbortSignal.timeout(Math.max(1, this.timeoutSecs) * 1000),
    });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
    }
    const data: any = await resp.json();
    return String(data?.choices?.[0]?.message?.content ?? "");
  }
}

/**
 * dsh `llm` 服务的窄结构契约（对应 dsh 0.1.2-alpha.3 的 LlmRuntime.stream()）。
 *
 * 不依赖 @deepseek-ai/dsh-llm 包：npm 上只有占位 RC 版本，workspace 真身不可
 * 独立安装。结构不匹配时任何一步都会抛错并被归一为 LLMError → 策略层降级
 * HOLD/KEEP_GRID + 连续失败告警，与「模型 ID 下线」同一套已验证的故障路径。
 */
export interface DshLlmLike {
  stream(options: {
    provider: string;
    model: string;
    system?: string;
    messages: unknown[];
    temperature?: number;
    signal?: AbortSignal;
  }): AsyncIterable<{ type: string; text?: string }>;
}

/** dsh 宿主后端：一次 stream() 聚合为整段文本。 */
export class DshLlmBackend implements LLMBackend {
  constructor(
    /** 惰性取服务：每次调用时解析，服务热替换/尚未就绪都能被正确感知 */
    private readonly getService: () => DshLlmLike | undefined,
    private readonly provider: string,
    readonly model: string,
    private readonly timeoutSecs: number,
  ) {}

  describe(): string {
    return `dsh llm 服务 provider=${this.provider} model=${this.model}`;
  }

  async chatOnce(system: string, user: string, temperature: number): Promise<string> {
    const llm = this.getService();
    if (!llm || typeof llm.stream !== "function") {
      throw new Error("dsh llm 服务不可用（未挂载 @deepseek-ai/dsh-llm 或服务未就绪）");
    }
    const signal = AbortSignal.timeout(Math.max(1, this.timeoutSecs) * 1000);
    // dsh 的 Message 要求 id/source 元数据；此处按 dsh-llm 的结构约定手工构造
    // 一条一次性 user 消息（id 仅需进程内唯一，source 标记外部产生）。
    const message = {
      id: `quantflow-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
      role: "user",
      content: [{ type: "text", text: user }],
      source: { kind: "user" },
    };
    let text = "";
    for await (const chunk of llm.stream({
      provider: this.provider,
      model: this.model,
      system,
      messages: [message],
      temperature,
      signal,
    })) {
      if (chunk?.type === "text-delta" && typeof chunk.text === "string") {
        text += chunk.text;
      }
    }
    return text;
  }
}

/**
 * OpenAI 兼容聊天补全客户端（统一重试壳）。
 *
 * 线程安全在 TS 单线程模型下退化为「无共享可变状态」——可被多个策略并用。
 */
export class LLMClient {
  readonly model: string;
  private readonly temperature: number;
  private readonly maxRetries: number;
  private readonly backend: LLMBackend;
  /** 重试退避的时间缩放（测试注入 0 免等待；生产恒为 1） */
  private readonly backoffScale: number;

  constructor(options: {
    backend: LLMBackend;
    model: string;
    temperature?: number;
    maxRetries?: number;
    backoffScale?: number;
  }) {
    this.backend = options.backend;
    this.model = options.model;
    this.temperature = options.temperature ?? 0.2;
    this.maxRetries = Math.max(1, Math.trunc(options.maxRetries ?? 3));
    this.backoffScale = options.backoffScale ?? 1;
  }

  describe(): string {
    return this.backend.describe();
  }

  /**
   * 发送一轮对话，返回助手回复文本。
   *
   * @throws LLMError 重试耗尽仍未获得非空回复
   */
  async chat(system: string, user: string, temperature?: number): Promise<string> {
    const temp = temperature ?? this.temperature;
    let lastError: unknown = null;
    for (let attempt = 1; attempt <= this.maxRetries; attempt++) {
      try {
        const content = await this.backend.chatOnce(system, user, temp);
        if (content && content.trim()) return content;
        // 必须带上后端标识：provider/model 名不被适配器识别时，流会正常结束但
        // 零分片，症状与限流/余额不足/网络抖动完全相同。不打印这两个值，运维
        // 只能看到「空回复」，会误判成密钥或网络问题（线上已踩过一次）。
        lastError = new LLMError(`LLM 返回空回复（${this.backend.describe()}）`);
      } catch (e) {
        // 网络/HTTP/解析异常统一按瞬时故障重试
        lastError = e;
      }
      if (attempt < this.maxRetries) {
        await sleep(Math.min(2.0 * attempt, MAX_BACKOFF_SECONDS) * 1000 * this.backoffScale);
      }
    }
    throw new LLMError(`LLM 调用失败（已重试 ${this.maxRetries} 次）: ${lastError}`);
  }
}

/**
 * 从 LLM 回复中提取首个 JSON 对象（保证返回普通对象，否则抛 Error）。
 *
 * 依次尝试：```json 围栏 → 整体解析 → 括号平衡扫描提取首个对象。
 * 平衡扫描正确处理字符串内的花括号与转义，线上验证过 19 类畸形输出。
 *
 * 返回值恒为对象：解析出数组/标量（如 LLM 输出 JSON 数组）时继续降级尝试，
 * 最终仍无对象则抛错——调用方的兜底路径（HOLD/KEEP_GRID + llm_ok=False）
 * 依赖这一契约，绝不能把非对象透传出去引发属性访问崩溃。
 */
export function extractJson(text: unknown): Record<string, unknown> {
  if (text !== null && typeof text === "object" && !Array.isArray(text)) {
    return text as Record<string, unknown>;
  }

  const raw = String(text ?? "").trim();
  if (!raw) throw new Error("LLM 回复为空");

  const fenced = /```json\s*([\s\S]*?)```/i.exec(raw);
  if (fenced && fenced[1].trim()) {
    try {
      const parsed = JSON.parse(fenced[1]);
      if (isPlainObject(parsed)) return parsed;
    } catch {
      // 围栏内容损坏时继续尝试其余提取策略
    }
  }

  try {
    const parsed = JSON.parse(raw);
    if (isPlainObject(parsed)) return parsed;
  } catch {
    // 整体解析失败继续降级
  }

  const parsed = JSON.parse(firstJsonObject(raw));
  if (!isPlainObject(parsed)) {
    throw new Error(`解析结果不是 JSON 对象（实际为 ${Array.isArray(parsed) ? "array" : typeof parsed}）`);
  }
  return parsed;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** 括号平衡扫描：返回文本中首个完整的 `{...}` 片段。 */
function firstJsonObject(text: string): string {
  let start = -1;
  let depth = 0;
  let inString = false;
  let escape = false;

  for (let idx = 0; idx < text.length; idx++) {
    const ch = text[idx];
    if (inString) {
      if (escape) escape = false;
      else if (ch === "\\") escape = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") {
      if (depth === 0) start = idx;
      depth += 1;
    } else if (ch === "}" && depth > 0) {
      depth -= 1;
      if (depth === 0 && start >= 0) return text.slice(start, idx + 1);
    }
  }
  throw new Error("未找到有效 JSON 对象");
}

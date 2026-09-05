/** 配置模块测试：默认值、覆盖、环境变量与保护链默认（Schemastery 形态）。 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  ConfigSchema,
  DEFAULT_PROTECTIONS,
  deepMerge,
  resolveRuntimeConfig,
  warnUnknownKeys,
  type QuantFlowConfigInput,
} from "../src/config.js";

const ENV_KEYS = ["HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_ACCOUNT_ADDRESS", "HYPERLIQUID_TESTNET", "LLM_API_KEY", "OPENAI_API_KEY", "QUANTFLOW_WEB_TOKEN", "RUN_MODE"];
let saved: Record<string, string | undefined> = {};

beforeEach(() => {
  saved = Object.fromEntries(ENV_KEYS.map((k) => [k, process.env[k]]));
  for (const k of ENV_KEYS) delete process.env[k];
  process.env.HYPERLIQUID_PRIVATE_KEY = "0x" + "1".repeat(64);
});
afterEach(() => {
  for (const k of ENV_KEYS) {
    if (saved[k] === undefined) delete process.env[k];
    else process.env[k] = saved[k];
  }
});

const validate = (input: unknown): QuantFlowConfigInput =>
  new (ConfigSchema as never as new (v: unknown) => QuantFlowConfigInput)(input);

describe("默认值", () => {
  it("空配置全默认（安全取向）：单账户模式合成 default 账户与默认保护链", () => {
    const cfg = resolveRuntimeConfig(validate({}));
    expect(cfg.accounts.length).toBe(1);
    const acct = cfg.accounts[0];
    expect(acct.name).toBe("default");
    expect(acct.trading.symbols).toEqual(["BTC"]);
    // 只有网格一种策略，默认开启
    expect(acct.trading.grid_enabled).toBe(true);
    expect(acct.protections.map((p) => p.name)).toEqual(["max_drawdown", "daily_loss", "consecutive_loss"]);
    expect(acct.llm.temperature).toBe(0); // 决策可回放
    expect(acct.grid.capital_ratio).toBe(0.5);
    expect(acct.grid.post_only).toBe(true);
    expect(acct.grid.level_trigger_stop_loss).toBe(false);
    expect(acct.grid.level_trigger_take_profit).toBe(false);
    expect(acct.grid.inventory_cap_ratio).toBe(0.7);
    // 错峰默认值：N 台引擎共用一个出口 IP，齐射会撞交易所限流
    expect(cfg.fleet.start_stagger_secs).toBe(2);
    expect(acct.grid.trend_side_only).toBe(true);
    expect(acct.grid.inventory_skew).toBe(0); // 未经回测证明，默认关
    expect(acct.grid.range_filter_er_max).toBe(0); // 同上
    expect(acct.trading.max_leverage).toBe(5);
    expect(acct.grid.force_neutral).toBe(true);
    expect(acct.exchange.testnet).toBe(true); // 默认测试网（安全取向）
    expect(acct.llm.provider).toBe("dsh"); // 寄生模式默认走宿主 llm
    expect(acct.paths.data_dir).toBe("data"); // 单账户模式不带 accounts/ 前缀
    expect(cfg.web.enabled).toBe(true);

    // 显式空数组 = 全关，必须被尊重（不能悄悄补回默认链）
    expect(resolveRuntimeConfig(validate({ protections: [] })).accounts[0].protections).toEqual([]);
    // 默认链是拷贝：运行期改一个账户不得污染模块常量与其它账户
    expect(acct.protections).toEqual(DEFAULT_PROTECTIONS);
    (acct.protections[0] as Record<string, unknown>).max_drawdown_pct = 0.99;
    expect(DEFAULT_PROTECTIONS[0].max_drawdown_pct).toBe(0.10);
  });

  it("缺少私钥抛错", () => {
    process.env.HYPERLIQUID_PRIVATE_KEY = "";
    expect(() => resolveRuntimeConfig(validate({}))).toThrow(/HYPERLIQUID_PRIVATE_KEY/);
  });
});

describe("配置覆盖", () => {
  it("覆盖值生效且符号统一大写", () => {
    const cfg = resolveRuntimeConfig(
      validate({
        llm: { base_url: "https://example.com/v1/", model: "my-model", temperature: 0.5 },
        trading: { symbols: ["eth", "btc"], grid_enabled: true, max_trade_amount: 250, max_leverage: 3 },
        grid: { interval_minutes: 10, width_min_pct: 0.03, barrier: { stop_loss_pct: 0.08 } },
      }),
    );
    const acct = cfg.accounts[0];
    expect(acct.llm.model).toBe("my-model");
    expect(acct.trading.symbols).toEqual(["ETH", "BTC"]); // 符号统一大写
    expect(acct.trading.grid_enabled).toBe(true);
    expect(acct.trading.max_trade_amount).toBe(250);
    expect(acct.grid.interval_minutes).toBe(10);
    expect(acct.grid.width_min_pct).toBe(0.03);
    expect(acct.grid.barrier).toEqual({ stop_loss_pct: 0.08 });
  });

  it("列表键误写成标量按单元素纠偏（不得拆成 B/T/C），空 symbols 直接抛错", () => {
    const cases: Array<[name: string, mutate: (input: QuantFlowConfigInput) => void, read: (cfg: ReturnType<typeof resolveRuntimeConfig>) => unknown, expected: unknown]> = [
      [
        "symbols 误写标量字符串",
        (input) => {
          (input.trading as { symbols: unknown }).symbols = "eth";
        },
        (cfg) => cfg.accounts[0].trading.symbols,
        ["ETH"],
      ],
      [
        "trend_filter_timeframes 误写标量字符串",
        (input) => {
          (input.grid as { trend_filter_timeframes: unknown }).trend_filter_timeframes = "15m";
        },
        (cfg) => cfg.accounts[0].grid.trend_filter_timeframes,
        ["15m"],
      ],
    ];
    for (const [name, mutate, read, expected] of cases) {
      const input = validate({});
      mutate(input);
      expect(read(resolveRuntimeConfig(input)), name).toEqual(expected);
    }
    // 空 symbols 无法纠偏：宁可启动失败，也不能空转成「什么都不交易」
    expect(() => resolveRuntimeConfig(validate({ trading: { symbols: [] } }))).toThrow(/symbols/);
  });
});

describe("环境变量", () => {
  it("testnet/account/LLM_API_KEY 解析，OPENAI_API_KEY 兼容回退", () => {
    process.env.HYPERLIQUID_TESTNET = "false";
    process.env.HYPERLIQUID_ACCOUNT_ADDRESS = "0xABC";
    process.env.LLM_API_KEY = "sk-test";
    const cfg = resolveRuntimeConfig(validate({}));
    expect(cfg.accounts[0].exchange.testnet).toBe(false);
    expect(cfg.accounts[0].exchange.account_address).toBe("0xABC");
    expect(cfg.accounts[0].llm.api_key).toBe("sk-test");

    delete process.env.LLM_API_KEY;
    process.env.OPENAI_API_KEY = "sk-openai";
    expect(resolveRuntimeConfig(validate({})).accounts[0].llm.api_key).toBe("sk-openai");
  });
});

describe("未知配置键告警", () => {
  // 拼错的键被 Schema 静默忽略，安全阀就成了「以为设了、其实没设」。
  it("未知顶层段、拼错的键、已移除的永续键、误写的 api_key 都必须告警", () => {
    const warnings: string[] = [];
    warnUnknownKeys(
      {
        trading: { max_position_notional_usd: 250, perp_enabled: true },
        llm: { api_key: "sk-xxx" },
        notification: { enabled: true },
      },
      (m) => warnings.push(m),
    );
    const text = warnings.join("\n");
    expect(text).toContain("notification"); // 未知顶层段
    expect(text).toContain("max_position_notional_usd"); // 该键属于 grid 段，写进 trading 无效
    expect(text).toContain("perp_enabled"); // 已移除的永续键
    expect(text).toContain("LLM_API_KEY"); // 密钥只走环境变量
  });
});

describe("deepMerge（看板覆盖层）", () => {
  it("对象递归、数组整体替换", () => {
    const merged = deepMerge(
      { trading: { symbols: ["BTC"], max_leverage: 5 }, grid: { interval_minutes: 5 } },
      { trading: { symbols: ["ETH", "SOL"] } },
    ) as Record<string, Record<string, unknown>>;
    expect(merged.trading.symbols).toEqual(["ETH", "SOL"]);
    expect(merged.trading.max_leverage).toBe(5);
    expect(merged.grid.interval_minutes).toBe(5);
  });
});

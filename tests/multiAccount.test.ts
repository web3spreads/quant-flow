/** 多账户运行时配置测试：地址×环境×策略交错组合、模板合并、隔离与安全闸门。 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ConfigSchema, resolveRuntimeConfig, type QuantFlowConfigInput } from "../src/config.js";
import { Fleet } from "../src/fleet.js";
import type { Dict } from "../src/trading/client.js";

const ENV_KEYS = [
  "HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_TESTNET", "LLM_API_KEY",
  "PK_GRID", "PK_ALT", "PK_SIM", "PK_LIVE",
];
let saved: Record<string, string | undefined> = {};
beforeEach(() => {
  saved = Object.fromEntries(ENV_KEYS.map((k) => [k, process.env[k]]));
  for (const k of ENV_KEYS) delete process.env[k];
  // 四把不同私钥 → 四个不同地址
  process.env.PK_GRID = "0x" + "1".repeat(64);
  process.env.PK_ALT = "0x" + "2".repeat(64);
  process.env.PK_SIM = "0x" + "3".repeat(64);
  process.env.PK_LIVE = "0x" + "4".repeat(64);
});
afterEach(() => {
  for (const k of ENV_KEYS) {
    if (saved[k] === undefined) delete process.env[k];
    else process.env[k] = saved[k];
  }
});

const validate = (input: unknown): QuantFlowConfigInput =>
  new (ConfigSchema as never as new (v: unknown) => QuantFlowConfigInput)(input);

/** 目标场景原型：4 套网格，交易对/环境/杠杆/保护链交错并行。 */
const FOUR_ACCOUNTS = {
  trading: { symbols: ["BTC"], max_leverage: 3 }, // 顶层=模板
  accounts: [
    { name: "grid-bot", private_key_env: "PK_GRID", testnet: true,
      trading: { grid_enabled: true, symbols: ["ETH"] } },
    { name: "alt-bot", private_key_env: "PK_ALT", testnet: true,
      trading: { symbols: ["SOL"] } },
    { name: "sim", private_key_env: "PK_SIM", testnet: true,
      grid: { interval_minutes: 15 } },
    { name: "live", private_key_env: "PK_LIVE", testnet: false,
      trading: { max_leverage: 2 }, protections: [{ name: "max_drawdown", max_drawdown_pct: 0.05 }] },
  ],
};

describe("多账户配置展开", () => {
  it("四账户交错组合：交易对/环境/私钥独立，顶层段作模板，目录与保护链按账户隔离", () => {
    const cfg = resolveRuntimeConfig(validate(FOUR_ACCOUNTS));
    expect(cfg.accounts.map((a) => a.name)).toEqual(["grid-bot", "alt-bot", "sim", "live"]);
    const [grid, alt, sim, live] = cfg.accounts;

    // 交易对交错（每账户一套独立网格）
    expect(grid.trading.grid_enabled).toBe(true);
    expect(grid.trading.symbols).toEqual(["ETH"]);
    expect(alt.trading.symbols).toEqual(["SOL"]);
    // 环境交错：3 测试网（模拟盘）+ 1 主网（实盘）
    expect([grid, alt, sim].every((a) => a.exchange.testnet)).toBe(true);
    expect(live.exchange.testnet).toBe(false);
    // 私钥各自来自指定环境变量，绝不串号
    expect(grid.exchange.private_key).toBe("0x" + "1".repeat(64));
    expect(live.exchange.private_key).toBe("0x" + "4".repeat(64));

    // 顶层段是模板：条目只写差异，其余继承；Schema 默认值仍填充
    expect(sim.trading.symbols).toEqual(["BTC"]);
    expect(sim.trading.max_leverage).toBe(3);
    expect(live.trading.max_leverage).toBe(2);
    expect(sim.grid.interval_minutes).toBe(15);
    expect(sim.grid.force_neutral).toBe(true);
    // 保护链同理：live 覆盖，其余继承默认链
    expect(live.protections).toEqual([{ name: "max_drawdown", max_drawdown_pct: 0.05 }]);
    expect(grid.protections.length).toBe(3);

    // 数据/日志目录按账户隔离（状态文件绝不互踩）
    const dataDirs = cfg.accounts.map((a) => a.paths.data_dir);
    expect(dataDirs).toEqual([
      "data/accounts/grid-bot", "data/accounts/alt-bot", "data/accounts/sim", "data/accounts/live",
    ]);
    expect(new Set(dataDirs).size).toBe(4);
    expect(grid.paths.log_dir).toBe("logs/accounts/grid-bot");
  });

  it("缺私钥报错点名环境变量", () => {
    delete process.env.PK_LIVE;
    expect(() => resolveRuntimeConfig(validate(FOUR_ACCOUNTS))).toThrow(/live.*PK_LIVE/);
  });

  it("非法账户配置拒绝启动（账户名同时是目录名与状态文件归属）", () => {
    const cases: [string, unknown, RegExp][] = [
      // 重名 → 共写同一份状态文件，是簿记灾难
      ["账户重名", { accounts: [{ name: "a", private_key_env: "PK_GRID" }, { name: "a", private_key_env: "PK_ALT" }] }, /重复/],
      // 路径穿越 → 写到账户目录之外
      ["非法账户名", { accounts: [{ name: "../escape", private_key_env: "PK_GRID" }] }, /非法/],
    ];
    for (const [label, input, pattern] of cases) {
      expect(() => resolveRuntimeConfig(validate(input)), label).toThrow(pattern);
    }
  });

  it("账户数量不设上限：16 账户展开各自独立", () => {
    const many = {
      accounts: Array.from({ length: 16 }, (_, i) => {
        process.env[`PK_N${i}`] = "0x" + (i + 10).toString(16).padStart(2, "0").repeat(32);
        return {
          name: `n${i}`,
          private_key_env: `PK_N${i}`,
          testnet: i % 4 !== 3,
          trading: { grid_enabled: true, symbols: [i % 2 === 0 ? "BTC" : "ETH"] },
        };
      }),
    };
    const cfg = resolveRuntimeConfig(validate(many));
    expect(cfg.accounts.length).toBe(16);
    expect(new Set(cfg.accounts.map((a) => a.paths.data_dir)).size).toBe(16);
    expect(new Set(cfg.accounts.map((a) => a.exchange.private_key)).size).toBe(16);
    expect(cfg.accounts.filter((a) => !a.exchange.testnet).length).toBe(4);
    for (let i = 0; i < 16; i++) delete process.env[`PK_N${i}`];
  });
});

describe("Fleet 聚合与安全闸门", () => {
  function fakeEngine(spec: {
    name: string; testnet: boolean; address: string;
    grid?: boolean;
    equity?: number; trades?: Dict[]; equitySeries?: Dict[];
  }): Dict {
    return {
      name: spec.name,
      isRunning: true,
      config: {
        name: spec.name,
        exchange: { testnet: spec.testnet },
        trading: { grid_enabled: spec.grid ?? false, symbols: ["BTC"] },
        paths: { data_dir: `data/accounts/${spec.name}`, log_dir: `logs/accounts/${spec.name}` },
      },
      client: { address: spec.address },
      llm: { describe: () => "fake" },
      orderManager: {
        getAvailableBalanceInfo: async () => ({ status: "ok", total: spec.equity ?? 100, available: 50, unrealized_pnl: 1 }),
        getCurrentPositions: async () => [],
      },
      logger: {
        readRecentJsonl: (kind: string) => (kind === "trades" ? spec.trades ?? [] : spec.equitySeries ?? []),
      },
    };
  }

  const bareFleet = (engines: Dict[]): Fleet => {
    const fleet = Object.create(Fleet.prototype) as Fleet;
    (fleet as Dict).engines = engines;
    (fleet as Dict).config = { accounts: engines.map((e) => e.config) };
    return fleet;
  };

  it("大盘总览：逐账户收益（今日/累计）与汇总", async () => {
    const today = new Date().toISOString();
    const engines = [
      fakeEngine({
        name: "grid-bot", testnet: true, address: "0xaaa", grid: true, equity: 200,
        trades: [
          { timestamp: today, pnl: 1.5, action: "GRID_ROUND_TRIP" },
          { timestamp: "2026-01-01T00:00:00Z", pnl: -0.5, action: "GRID_NET_CLOSE" },
          { timestamp: today, pnl: null, action: "GRID_BUY" }, // 无归因 pnl 的挂单记录不计
        ],
        equitySeries: [{ timestamp: today, equity: 200 }, { timestamp: today, equity: 199 }],
      }),
      fakeEngine({ name: "live", testnet: false, address: "0xbbb", grid: true, equity: 300 }),
    ];
    const fleet = bareFleet(engines);
    const overview = await fleet.overview();

    expect(overview.accounts.length).toBe(2);
    const gridAcct = overview.accounts[0];
    expect(gridAcct.env).toBe("测试网");
    expect(gridAcct.realized_pnl_total).toBeCloseTo(1.0); // 1.5 - 0.5
    expect(gridAcct.realized_pnl_today).toBeCloseTo(1.5);
    expect(gridAcct.equity_history.length).toBe(2);
    expect(overview.accounts[1].env).toBe("主网");
    expect(overview.totals.count).toBe(2);
    expect(overview.totals.equity).toBe(500);
    expect(overview.totals.testnet_count).toBe(1);
    expect(overview.totals.mainnet_count).toBe(1);
  });

  it("同一地址×环境被两个账户使用：拒绝（互相强平/互撤保护单）；同地址不同环境放行", () => {
    const same = bareFleet([
      fakeEngine({ name: "a", testnet: true, address: "0xSAME" }),
      fakeEngine({ name: "b", testnet: true, address: "0xsame" }), // 大小写视为同址
    ]);
    expect(() => (same as Dict).assertNoSharedAddress()).toThrow(/同一地址/);

    // 测试网与主网互不相干，同址允许双开
    const split = bareFleet([
      fakeEngine({ name: "a", testnet: true, address: "0xSAME" }),
      fakeEngine({ name: "b", testnet: false, address: "0xSAME" }),
    ]);
    expect(() => (split as Dict).assertNoSharedAddress()).not.toThrow();
  });
});

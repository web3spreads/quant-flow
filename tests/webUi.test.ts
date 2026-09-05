/**
 * 看板前端测试：在 node:vm 里加载页面内联脚本，直接对**纯渲染函数**断言。
 *
 * 看板是插件里唯一没有类型保护的一层（客户端 JS 以字符串形式内联），
 * 因此这里只守三条会赔钱/看错账户的线：① 脚本能被解析和执行；
 * ② 多账户身份与环境标识不串台（实盘警示、身份色、账户名转义）；
 * ③ 各视图在真实数据形状下渲染出内容而不是抛异常或白屏。
 */
import { describe, expect, it, beforeAll } from "vitest";
import vm from "node:vm";
import { DASHBOARD_HTML } from "../src/web/page.js";
import { ConfigSchema } from "../src/config.js";

type Ctx = Record<string, unknown> & { [key: string]: any };

function fakeEl(): Record<string, unknown> {
  return {
    innerHTML: "",
    textContent: "",
    className: "",
    value: "",
    style: {},
    dataset: {},
    setAttribute() {},
    getAttribute() {
      return null;
    },
    addEventListener() {},
    closest() {
      return null;
    },
    getBoundingClientRect() {
      return { width: 100, height: 40 };
    },
  };
}

/** 页面脚本在受控沙箱里跑：__QF_TEST__ 让它只定义函数、不启动轮询与取数。 */
function loadDashboard(): Ctx {
  const script = DASHBOARD_HTML.split("<script>")[1]?.split("</scr" + "ipt>")[0];
  expect(script, "页面必须包含内联脚本").toBeTruthy();
  const store: Record<string, string> = {};
  const ctx: Ctx = {
    __QF_TEST__: true,
    console,
    Date,
    Math,
    JSON,
    Number,
    String,
    Object,
    Array,
    isFinite,
    encodeURIComponent,
    setTimeout: () => 0,
    clearTimeout: () => undefined,
    localStorage: {
      getItem: (k: string) => (k in store ? store[k] : null),
      setItem: (k: string, v: string) => {
        store[k] = v;
      },
    },
    document: {
      title: "",
      hidden: false,
      body: fakeEl(),
      querySelector: () => fakeEl(),
      querySelectorAll: () => [],
      addEventListener: () => undefined,
    },
    fetch: () => Promise.reject(new Error("测试沙箱禁止网络")),
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(script as string, ctx, { filename: "dashboard.js" });
  return ctx;
}

const ROSTER = [
  {
    name: "alpha",
    running: true,
    testnet: true,
    env: "测试网",
    address: "0x1111111111111111111111111111111111111111",
    strategies: { grid: true, symbols: ["BTC"], grid_symbol: "BTC" },
    llm: "dsh/deepseek",
    data_dir: "/data/accounts/alpha",
    log_dir: "/logs/accounts/alpha",
  },
  {
    name: "bravo",
    running: true,
    testnet: false,
    env: "主网",
    address: "0x2222222222222222222222222222222222222222",
    strategies: { grid: true, symbols: ["ETH"], grid_symbol: "ETH" },
    llm: "dsh/deepseek",
    data_dir: "/data/accounts/bravo",
    log_dir: "/logs/accounts/bravo",
  },
];

const T0 = Date.parse("2026-09-03T00:00:00Z");
const hist = (base: number, n: number) =>
  Array.from({ length: n }, (_v, i) => ({ t: new Date(T0 + i * 3600_000).toISOString(), equity: base + i * 3 }));

const FLEET = {
  accounts: [
    {
      ...ROSTER[0],
      balance_status: "ok",
      equity: 1000,
      available: 800,
      unrealized_pnl: 12.5,
      positions_count: 1,
      positions_query_failed: false,
      realized_pnl_total: 34.5,
      realized_pnl_today: 2.25,
      trades_today: 7,
      equity_history: hist(1000, 12),
    },
    {
      ...ROSTER[1],
      balance_status: "ok",
      equity: 500,
      available: 100,
      unrealized_pnl: -8,
      positions_count: 2,
      positions_query_failed: false,
      realized_pnl_total: -12.5,
      realized_pnl_today: -3.5,
      trades_today: 2,
      equity_history: hist(500, 12),
    },
  ],
  totals: {
    count: 2,
    running: 2,
    equity: 1500,
    available: 900,
    unrealized_pnl: 4.5,
    realized_pnl_total: 22,
    realized_pnl_today: -1.25,
    testnet_count: 1,
    mainnet_count: 1,
  },
};

let ctx: Ctx;
beforeAll(() => {
  ctx = loadDashboard();
  ctx.ROSTER = ROSTER;
});

describe("看板脚本", () => {
  it("内联脚本可解析并执行、导出全部视图函数，且页面零外部资源", () => {
    for (const fn of ["fleetHtml", "overviewHtml", "decisionsHtml", "tradesHtml", "gridHtml", "riskHtml", "backtestHtml", "configHtml", "chartLine", "chartLadder"]) {
      expect(typeof ctx[fn], fn + " 必须存在").toBe("function");
    }
    expect(DASHBOARD_HTML).not.toMatch(/src="https?:/);
    expect(DASHBOARD_HTML).not.toMatch(/href="https?:/);
  });
});

describe("大盘视图", () => {
  it("渲染全部账户、合计与图表，且两个账户拿到不同的身份色", () => {
    const html = ctx.fleetHtml(FLEET, "overview") as string;
    expect(html).toContain("alpha");
    expect(html).toContain("bravo");
    expect(html).toContain("大盘总控");
    expect(html).toContain("<svg");
    // 账户矩阵每行一条身份色条
    expect((html.match(/class="bar"/g) || []).length).toBeGreaterThanOrEqual(2);
    expect(ctx.acolor("alpha")).not.toBe(ctx.acolor("bravo"));
  });

  it("含主网账户时整体走实盘警示态，且逐账户环境徽标不同", () => {
    const html = ctx.fleetHtml(FLEET, "overview") as string;
    expect(html).toContain("scope live");
    expect(html).toContain("主网 · 实盘");
    expect(html).toContain("测试网 · 模拟");
    expect(html).toContain("含 1 个实盘账户");
  });

  it("归一化模式把各账户首个快照拉平到 100，绝对值模式保留原始净值", () => {
    const series = ctx.fleetEquitySeries(FLEET.accounts, "pct") as { pts: number[][] }[];
    expect(series[0].pts[0][1]).toBe(100);
    expect(series[1].pts[0][1]).toBe(100);
    const abs = ctx.fleetEquitySeries(FLEET.accounts, "abs") as { pts: number[][] }[];
    expect(abs[1].pts[0][1]).toBe(500);
  });

  it("风控矩阵按账户 × 插件排布，暂停状态直接可见", () => {
    const html = ctx.fleetRiskHtml([
      { account: ROSTER[0], rows: [{ name: "daily_loss", enabled: true, config: {}, state: { is_paused: false, daily_start_equity: 100 } }] },
      { account: ROSTER[1], rows: [{ name: "daily_loss", enabled: true, config: {}, state: { is_paused: true, pause_reason: "日亏超限" } }] },
    ]) as string;
    expect(html).toContain("daily_loss");
    expect(html).toContain("已暂停");
    expect(html).toContain("日亏超限");
  });

  it("恶意账户名被转义，既不注入页面也不破坏点击处理器", () => {
    for (const evilName of ["a'};alert(1);//", "<img src=x onerror=alert(1)>"]) {
      const evil = JSON.parse(JSON.stringify(FLEET));
      evil.accounts[1].name = evilName;
      const html = ctx.fleetHtml(evil, "overview") as string;
      // 跳转走引用下标，名字绝不出现在任何事件处理器里
      expect(html, evilName).toMatch(/onclick="goNav\(\d+\)"/);
      expect(html, evilName).not.toMatch(/onclick="[^"]*alert/);
      // 原始名字只以被转义的形式出现
      expect(html, evilName).not.toContain(evilName);
    }
  });
});

describe("账户视图", () => {
  const OVERVIEW = {
    plugin: { name: "dsh-plugin-quant-flow", version: "test" },
    account: "bravo",
    accounts: ROSTER,
    engine: {
      running: true,
      started_at: T0,
      testnet: false,
      account: "0x2222222222222222222222222222222222222222",
      symbols: ["ETH"],
      grid_enabled: true,
      timeframe: "1h",
      grid_interval_minutes: 30,
      llm: "dsh/deepseek-v4",
    },
    balance: { status: "ok", total: 500, available: 100, unrealized_pnl: -8 },
    positions: [{ coin: "ETH", szi: "-0.5", entryPx: "3000", positionValue: "1500", unrealizedPnl: "-8", leverage: { value: 3 } }],
    positions_query_failed: false,
    open_orders: [{ oid: 9001, coin: "ETH", side: "B", limitPx: "2900", sz: "0.5", reduceOnly: false }],
    open_orders_query_failed: false,
    strategies: { grid: { symbol: "ETH", health: { llm_failure_streak: 0, idle_streak: 0 } } },
  };
  const EQUITY = hist(500, 8)
    .map((e) => ({ timestamp: e.t, equity: e.equity, available: e.equity - 400, position_notional: 1500 }))
    .reverse();
  const GRID = {
    enabled: true,
    symbol: "BTC",
    current_price: 60000,
    config: { parameters: { lower_price: 58000, upper_price: 62000, grid_num: 3, amount_per_grid: 50 } },
    levels: [
      { id: "L1", price: "59000", side: "LONG", state: "OPEN_FILLED", open_fill_price: "59010", open_fill_amount: "0.001", close_order_id: 1, round_trip_count: 2, cumulative_pnl: "1.2" },
      { id: "L2", price: "61000", side: "SHORT", state: "IDLE", open_fill_price: null, open_fill_amount: null, close_order_id: null, round_trip_count: 0, cumulative_pnl: "0" },
    ],
    pnl: { realized_pnl: 1.2, unrealized_pnl: -0.3, net_pnl: 0.9, completed_round_trips: 2, open_positions: 1, avg_entry_price: 59010 },
    barrier: {},
    rebuild_cooldown_remaining: 120,
    summary: "网格摘要文本",
    strategy_health: {},
    pending_emergency_close: {},
  };
  const SCHEMA = (ConfigSchema as unknown as { toJSON(): Record<string, unknown> }).toJSON();

  it("作用域头：主网走警示态并带完整身份信息，测试网不带实盘警示", () => {
    ctx.STATE.account = "bravo";
    const live = ctx.accountScopeHeader(OVERVIEW) as string;
    expect(live).toContain("scope live");
    expect(live).toContain("⚠ 主网 · 实盘");
    expect(live).toContain("bravo");
    expect(live).toContain("0x2222…2222");
    expect(live).toContain("/data/accounts/bravo");

    ctx.STATE.account = "alpha";
    const sim = ctx.accountScopeHeader({ engine: { testnet: true, running: true, symbols: ["BTC"] }, balance: {} }) as string;
    expect(sim).not.toContain("scope live");
    expect(sim).toContain("测试网 · 模拟");
  });

  it("各视图在真实数据形状下都渲染出内容且不抛异常", () => {
    ctx.STATE.account = "bravo";
    ctx.DEC_FILTER = { strategy: "", status: "", symbol: "" };
    const views: [string, () => string][] = [
      ["总览", () => ctx.overviewHtml(OVERVIEW, EQUITY)],
      ["决策", () => ctx.decisionsHtml([
        { timestamp: "2026-09-03T01:00:00Z", strategy: "grid", symbol: "BTC", decision: "UPDATE_GRID", confidence: 0.8, status: "SUCCESS", action_details: { reason: "重建网格" } },
        { timestamp: "2026-09-03T02:00:00Z", strategy: "grid", symbol: "BTC", decision: "KEEP_GRID", confidence: 0.5, status: "FAILED", error_message: "LLM 超时" },
      ])],
      ["交易", () => ctx.tradesHtml([
        { timestamp: "2026-09-03T01:00:00Z", action: "GRID_ROUND_TRIP", symbol: "BTC", amount: 0.01, price: 60000, status: "SUCCESS", pnl: 1.5, fee: 0.02, crossed: false, reason: "轮回" },
        { timestamp: "2026-09-03T02:00:00Z", action: "GRID_ROUND_TRIP", symbol: "BTC", amount: 0.01, price: 60500, status: "SUCCESS", pnl: -0.5, fee: 0.02, crossed: true, reason: "轮回" },
      ])],
      ["网格", () => ctx.gridHtml(GRID)],
      ["风控", () => ctx.riskHtml([
        { name: "max_drawdown", enabled: true, config: { max_drawdown_pct: 0.1 }, state: { is_paused: true, pause_reason: "回撤 12%" } },
      ])],
      ["回测", () => ctx.backtestHtml({
        generatedAt: new Date(T0).toISOString(),
        elapsedMs: 1000,
        params: { initialEquity: 1000 },
        summary: { grid: { n: 5, meanPct: 1.2, medianPct: 0.9, sdPct: 2, winRate: 0.6, tStat: 1.4, meanFeePctOfEquity: 0.5 } },
        runs: [
          { symbol: "BTC", interval: "15m", strategy: "grid", days: 30, returnPct: 1.2, maxDrawdownPct: 3, benchmarkPct: -2, fees: { pctOfInitial: 0.4 }, fills: { takerRatio: 0.2 }, equityCurve: [{ equity: 1000 }, { equity: 1012 }] },
        ],
      })],
      ["配置", () => ctx.configHtml({
        schema: SCHEMA,
        base: { trading: { symbols: ["BTC"] }, accounts: [{ name: "alpha", private_key_env: "K1", testnet: true, trading: {} }] },
        overrides: { grid: { interval_minutes: 15 } },
      })],
    ];
    for (const [label, render] of views) {
      const html = render();
      expect(html.length, label + " 页不应为空").toBeGreaterThan(200);
      expect(html, label + " 页应渲染出标签").toContain("<");
    }
    // 风控暂停原因与配置表单路径是运维读账的入口，单独钉死
    expect(views[4][1]()).toContain("回撤 12%");
    const cfg = views[6][1]();
    expect(cfg).toContain('data-path="trading.symbols"');
    expect(cfg).toContain('data-path="accounts"');
  });

  it("空/未启用形态给出说明而不是白屏", () => {
    expect(ctx.gridHtml({ enabled: false })).toContain("未启用网格策略");
    expect(ctx.backtestHtml({ empty: true, hint: "尚无回测报告" })).toContain("尚无回测报告");
    expect(ctx.configHtml({ schema: SCHEMA, base: { accounts: [] }, overrides: {} })).toContain("单账户模式");
  });
});

describe("图表工具", () => {
  it("空数据给占位、折线图带路径与热区、环形图与条形图分色正确", () => {
    // 空数据一律不抛异常
    expect(ctx.chartLine({ series: [] })).toContain("暂无数据");
    expect(ctx.chartBarsH([])).toContain("暂无数据");
    expect(ctx.chartDonut([])).toContain("暂无数据");
    expect(ctx.chartLadder({ levels: [] })).toContain("暂无层级");
    expect(ctx.sparkline([])).toContain("<svg");

    const line = ctx.chartLine({
      series: [{ name: "净值", color: "#4d6bfe", pts: [[T0, 100], [T0 + 3600_000, 110], [T0 + 7200_000, 105]] }],
      h: 200,
      area: true,
    }) as string;
    expect(line).toContain("<svg");
    expect(line).toContain("<path");
    expect(line).toContain('class="hbg"');

    // 单账户占比 100% 时仍是完整圆环
    const donut = ctx.chartDonut([{ label: "only", value: 10, color: "#4d6bfe" }]) as string;
    expect(donut).toContain("<path");
    expect(donut).toContain("100.0%");

    // 盈亏分色不能反：绿正红负
    const bars = ctx.chartBarsH([{ label: "a", value: 5 }, { label: "b", value: -3 }]) as string;
    expect(bars).toContain("#2ebd85");
    expect(bars).toContain("#f6465d");
  });
});

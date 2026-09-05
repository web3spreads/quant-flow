/**
 * 看板端到端渲染流程：在假 DOM + 假 fetch 上真正跑一遍 boot() 与作用域切换。
 *
 * 纯函数测试（webUi.test.ts）覆盖不到外壳：取数路径拼装、侧栏渲染、作用域切换、
 * 实盘警示轨条的开关。这里把它们跑通——没有浏览器，但足以让「点开就白屏」
 * 这类错误在 CI 里挂掉而不是在生产上被看到。
 */
import { describe, expect, it, beforeEach } from "vitest";
import vm from "node:vm";
import { DASHBOARD_HTML } from "../src/web/page.js";

type Any = Record<string, any>;

const T0 = Date.parse("2026-09-03T00:00:00Z");
const hist = (base: number, n: number) =>
  Array.from({ length: n }, (_v, i) => ({ t: new Date(T0 + i * 3600_000).toISOString(), equity: base + i }));

const ROSTER = [
  { name: "sim-a", running: true, testnet: true, env: "测试网", address: "0xaaaa000000000000000000000000000000000000",
    strategies: { grid: true, symbols: ["BTC"], grid_symbol: "BTC" },
    llm: "dsh/deepseek", data_dir: "/d/sim-a", log_dir: "/l/sim-a" },
  { name: "live-b", running: true, testnet: false, env: "主网", address: "0xbbbb000000000000000000000000000000000000",
    strategies: { grid: true, symbols: ["ETH"], grid_symbol: "ETH" },
    llm: "dsh/deepseek", data_dir: "/d/live-b", log_dir: "/l/live-b" },
];

function fixtureFor(url: string): unknown {
  const account = /account=([^&]+)/.exec(url)?.[1];
  if (url.startsWith("/api/accounts")) return ROSTER;
  if (url.startsWith("/api/fleet")) {
    return {
      accounts: ROSTER.map((r, i) => ({
        ...r, balance_status: "ok", equity: 1000 - i * 100, available: 500, unrealized_pnl: 1,
        positions_count: 0, positions_query_failed: false, realized_pnl_total: 1, realized_pnl_today: 0.5,
        trades_today: 2, equity_history: hist(1000 - i * 100, 6),
      })),
      totals: { count: 2, running: 2, equity: 1900, available: 1000, unrealized_pnl: 2,
        realized_pnl_total: 2, realized_pnl_today: 1, testnet_count: 1, mainnet_count: 1 },
    };
  }
  if (url.startsWith("/api/overview")) {
    const r = ROSTER.find((x) => x.name === account) ?? ROSTER[0];
    return {
      account: r.name, accounts: ROSTER,
      engine: { running: true, started_at: T0, testnet: r.testnet, account: r.address, symbols: r.strategies.symbols,
        grid_enabled: r.strategies.grid, timeframe: "1h", grid_interval_minutes: 30, llm: r.llm },
      balance: { status: "ok", total: 1000, available: 500, unrealized_pnl: 1 },
      positions: [], positions_query_failed: false, open_orders: [], open_orders_query_failed: false,
      strategies: { grid: r.strategies.grid ? { symbol: r.strategies.grid_symbol, health: { llm_failure_streak: 0, idle_streak: 0 } } : null },
    };
  }
  if (url.startsWith("/api/equity")) return hist(1000, 6).map((e) => ({ timestamp: e.t, equity: e.equity, available: 500, position_notional: 100 })).reverse();
  if (url.startsWith("/api/decisions")) return [];
  if (url.startsWith("/api/trades")) return [];
  if (url.startsWith("/api/grid")) return { enabled: false };
  if (url.startsWith("/api/protections")) return [{ name: "daily_loss", enabled: true, config: {}, state: { is_paused: false, daily_start_equity: 1000 } }];
  if (url.startsWith("/api/backtests")) return { empty: true, hint: "尚无回测报告" };
  throw new Error("未知接口 " + url);
}

function makeCtx(): { ctx: Any; el: (sel: string) => Any; calls: string[] } {
  const script = DASHBOARD_HTML.split("<script>")[1].split("</scr" + "ipt>")[0];
  const store: Record<string, string> = {};
  const els: Record<string, Any> = {};
  const el = (sel: string) => {
    if (!els[sel]) {
      els[sel] = { innerHTML: "", textContent: "", className: "", value: "", style: {}, dataset: {},
        addEventListener() {}, getAttribute: () => null, closest: () => null,
        getBoundingClientRect: () => ({ width: 10, height: 10 }) };
    }
    return els[sel];
  };
  const calls: string[] = [];
  const ctx: Any = {
    console, Date, Math, JSON, Number, String, Object, Array, Promise, isFinite, encodeURIComponent,
    setTimeout: () => 0, clearTimeout: () => undefined,
    localStorage: { getItem: (k: string) => (k in store ? store[k] : null), setItem: (k: string, v: string) => { store[k] = v; } },
    document: { title: "", hidden: false, body: el("body"), querySelector: el, querySelectorAll: () => [], addEventListener() {} },
    fetch: (url: string) => {
      calls.push(url);
      return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(fixtureFor(url)) });
    },
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(script, ctx, { filename: "dashboard.js" });
  return { ctx, el, calls };
}

/** 冲干净 fetch 链上的 microtask（假 fetch 全是已决 Promise）。 */
const flush = async () => {
  for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r));
};

describe("看板启动与作用域切换", () => {
  let h: ReturnType<typeof makeCtx>;
  beforeEach(() => {
    h = makeCtx();
  });

  it("boot() 拉名册、渲染侧栏与大盘主视图", async () => {
    h.ctx.boot();
    await flush();
    expect(h.calls[0]).toBe("/api/accounts");
    expect(h.el("#side").innerHTML).toContain("sim-a");
    expect(h.el("#side").innerHTML).toContain("live-b");
    expect(h.el("#side").innerHTML).toContain("大盘总控");
    expect(h.el("#view").innerHTML).toContain("账户矩阵");
    expect(h.el("#ts").textContent).toContain("更新于");
  });

  it("切到主网账户：取数带账户名并进入实盘警示态，切回测试网后复位", async () => {
    h.ctx.boot();
    await flush();
    h.calls.length = 0;

    h.ctx.go("account", "live-b");
    await flush();
    expect(h.calls.some((u) => u.indexOf("/api/overview?account=live-b") === 0)).toBe(true);
    expect(h.ctx.document.title).toContain("live-b");
    expect(h.ctx.document.body.className).toBe("livemode");
    expect(h.el("#liverail").style.display).toBe("block");
    expect(h.el("#view").innerHTML).toContain("⚠ 主网 · 实盘");

    // 切回测试网：警示态复位，不残留上一个账户的标识
    h.ctx.go("account", "sim-a");
    await flush();
    expect(h.ctx.document.body.className).toBe("");
    expect(h.el("#liverail").style.display).toBe("none");
    const html = h.el("#view").innerHTML as string;
    expect(html).toContain("sim-a");
    expect(html).not.toContain("live-b");
    expect(html).toContain("测试网 · 模拟");

    // 全局页（回测/配置）读的是全局 data_dir，绝不能带上当前账户
    h.calls.length = 0;
    h.ctx.go("backtest");
    await flush();
    expect(h.calls.some((u) => u.indexOf("/api/backtests") === 0 && u.indexOf("account=") < 0)).toBe(true);
    expect(h.el("#view").innerHTML).toContain("尚无回测报告");
  });

  it("浏览器禁用站点数据（localStorage 抛异常）时依然能启动", async () => {
    const script = DASHBOARD_HTML.split("<script>")[1].split("</scr" + "ipt>")[0];
    const els: Record<string, Any> = {};
    const el = (sel: string) => {
      if (!els[sel]) {
        els[sel] = { innerHTML: "", textContent: "", className: "", value: "", style: {}, dataset: {},
          addEventListener() {}, getAttribute: () => null, closest: () => null,
          getBoundingClientRect: () => ({ width: 10, height: 10 }) };
      }
      return els[sel];
    };
    const boom = () => {
      throw new Error("SecurityError: 站点数据被禁用");
    };
    const ctx: Any = {
      console, Date, Math, JSON, Number, String, Object, Array, Promise, isFinite, encodeURIComponent,
      setTimeout: () => 0, clearTimeout: () => undefined,
      localStorage: { getItem: boom, setItem: boom },
      document: { title: "", hidden: false, body: el("body"), querySelector: el, querySelectorAll: () => [], addEventListener() {} },
      fetch: (url: string) => Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(fixtureFor(url)) }),
    };
    ctx.window = ctx;
    ctx.globalThis = ctx;
    vm.createContext(ctx);
    vm.runInContext(script, ctx, { filename: "dashboard.js" });
    ctx.boot();
    await flush();
    expect(el("#view").innerHTML).toContain("账户矩阵");
    expect(el("#side").innerHTML).toContain("sim-a");
  });

  it("接口报错时给出错误面板而不是白屏", async () => {
    h.ctx.boot();
    await flush();
    h.ctx.fetch = () => Promise.resolve({ status: 500, ok: false, json: () => Promise.resolve({ error: "引擎未就绪" }) });
    h.ctx.render();
    await flush();
    expect(h.el("#view").innerHTML).toContain("加载失败");
    expect(h.el("#view").innerHTML).toContain("引擎未就绪");
  });
});

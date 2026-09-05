/** 看板服务测试：配置校验闸门、敏感信息脱敏、令牌鉴权。 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { WebConsole } from "../src/web/server.js";
import { OVERRIDES_FILENAME } from "../src/config.js";
import { makeQuietLogger, makeTempDir } from "./support.js";
import type { RuntimeConfig } from "../src/config.js";
import type { Engine } from "../src/engine.js";
import type { Dict } from "../src/trading/client.js";

let saved: string | undefined;
beforeEach(() => {
  saved = process.env.HYPERLIQUID_PRIVATE_KEY;
  process.env.HYPERLIQUID_PRIVATE_KEY = "0x" + "1".repeat(64);
});
afterEach(() => {
  if (saved === undefined) delete process.env.HYPERLIQUID_PRIVATE_KEY;
  else process.env.HYPERLIQUID_PRIVATE_KEY = saved;
});

function makeFakeEngine(): { engine: Engine; applied: RuntimeConfig[] } {
  const applied: RuntimeConfig[] = [];
  const logger = makeQuietLogger();
  const engine = {
    logger,
    isRunning: true,
    startedAt: Date.now(),
    config: {
      name: "default",
      llm: { provider: "dsh", dsh_provider: "deepseek", base_url: "", model: "m", temperature: 0.2, timeout: 120, api_key: "sk-secret" },
      exchange: { private_key: "0xdeadbeef", account_address: null, testnet: true },
      trading: { symbols: ["BTC"] },
      grid: {},
      web: { enabled: true, host: "127.0.0.1", port: 0, token: "" },
      paths: { data_dir: "data", log_dir: "logs" },
      protections: [],
    },
    llm: { describe: () => "fake-llm" },
    orderManager: {
      getAvailableBalanceInfo: async () => ({ status: "ok", total: 100, available: 90, unrealized_pnl: 0 }),
      getCurrentPositions: async () => [],
    },
    client: { getOpenOrders: async () => [] },
    gridStrategy: null,
    protectionManager: null,
  } as unknown as Engine;
  return { engine, applied };
}

function makeFakeFleet(engine: Engine): unknown {
  return {
    config: { accounts: [engine.config], web: { enabled: true, host: "127.0.0.1", port: 0, token: "" }, paths: { data_dir: "data", log_dir: "logs" } },
    engines: [engine],
    byName: (name?: string | null) => (!name || name === "default" ? engine : undefined),
    overview: async () => ({ accounts: [{ name: "default" }], totals: { count: 1 } }),
    roster: () => [
      { name: "default", running: true, testnet: true, env: "测试网", address: "0xabc", strategies: { grid: true, symbols: ["BTC"], grid_symbol: "BTC" }, llm: "fake-llm", data_dir: "data", log_dir: "logs" },
    ],
  };
}

async function startConsole(token = ""): Promise<{
  base: string;
  web: WebConsole;
  dataDir: string;
  applied: RuntimeConfig[];
}> {
  const { engine, applied } = makeFakeEngine();
  const dataDir = makeTempDir();
  const web = new WebConsole({
    getFleet: () => makeFakeFleet(engine) as never,
    logger: makeQuietLogger(),
    baseConfig: { trading: { symbols: ["BTC"] } },
    dataDir,
    host: "127.0.0.1",
    port: 0,
    token,
    applyConfig: async (cfg) => {
      applied.push(cfg);
    },
    pluginVersion: "test",
  });
  await web.start();
  const address = (web as never as { server: { address(): { port: number } } }).server.address();
  return { base: `http://127.0.0.1:${address.port}`, web, dataDir, applied };
}

describe("看板配置 API", () => {
  it("PUT 覆盖闸门：合法值 Schema 校验 → 落盘 → 热应用；非法值被拒绝，不落盘不热应用", async () => {
    const { base, web, dataDir, applied } = await startConsole();
    try {
      const ok = await fetch(`${base}/api/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides: { grid: { interval_minutes: 15 }, trading: { grid_enabled: true } } }),
      });
      expect(ok.status).toBe(200);
      expect(((await ok.json()) as Dict).ok).toBe(true);
      expect(applied.length).toBe(1);
      expect(applied[0].accounts[0].grid.interval_minutes).toBe(15);
      expect(applied[0].accounts[0].trading.grid_enabled).toBe(true);
      // 覆盖层已原子落盘
      const onDisk = JSON.parse(fs.readFileSync(path.join(dataDir, OVERRIDES_FILENAME), "utf-8"));
      expect(onDisk.grid.interval_minutes).toBe(15);

      // 非法值绝不带病重配：既不热应用，也不污染已落盘的覆盖层
      const bad = await fetch(`${base}/api/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides: { grid: { interval_minutes: "不是数字" } } }),
      });
      expect(bad.status).toBe(400);
      expect(applied.length).toBe(1);
      const after = JSON.parse(fs.readFileSync(path.join(dataDir, OVERRIDES_FILENAME), "utf-8"));
      expect(after.grid.interval_minutes).toBe(15);
    } finally {
      await web.stop();
    }
  });

  it("GET /api/config 脱敏：私钥与 API Key 绝不进 HTTP 响应", async () => {
    const { base, web } = await startConsole();
    try {
      const body = JSON.stringify(await (await fetch(`${base}/api/config`)).json());
      expect(body).not.toContain("0xdeadbeef");
      expect(body).not.toContain("sk-secret");
    } finally {
      await web.stop();
    }
  });

  it("GET /api/accounts 返回账户名册（无 I/O，侧栏渲染用）", async () => {
    const { base, web } = await startConsole();
    try {
      const rows = (await (await fetch(`${base}/api/accounts`)).json()) as Dict[];
      expect(Array.isArray(rows)).toBe(true);
      expect(rows[0].name).toBe("default");
      expect(rows[0].testnet).toBe(true);
      // 名册同样不得携带私钥
      expect(JSON.stringify(rows)).not.toContain("0xdeadbeef");
    } finally {
      await web.stop();
    }
  });

  it("令牌鉴权：无 token 401，带 token 放行", async () => {
    const { base, web } = await startConsole("secret-token");
    try {
      expect((await fetch(`${base}/api/overview`)).status).toBe(401);
      const ok = await fetch(`${base}/api/overview`, { headers: { Authorization: "Bearer secret-token" } });
      expect(ok.status).toBe(200);
    } finally {
      await web.stop();
    }
  });
});

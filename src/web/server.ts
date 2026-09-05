/**
 * 内置 Web 看板服务（零依赖，node:http）。
 *
 * 一个端口打开整套运行视图：总览（余额/持仓/挂单/网格状态）、决策时间线、
 * 成交与盈亏归因、网格层级实况、风控插件状态、以及
 * **全部配置的网页设置**（表单由 Schemastery Schema 自动生成，改 Schema 即改表单）。
 *
 * 配置写入 data/config.overrides.json（覆盖层），保存后触发引擎热重配。
 * 敏感信息（私钥/API Key）不经过本服务：它们只活在环境变量里。
 *
 * 安全：默认只监听 127.0.0.1。设置环境变量 QUANTFLOW_WEB_TOKEN 后，
 * 所有 /api/* 请求须携带 Bearer Token（页面会引导输入并存于浏览器本地）。
 */

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { URL } from "node:url";
import { ConfigSchema, deepMerge, loadOverrides, saveOverrides, resolveRuntimeConfig, warnUnknownKeys, type EngineConfig, type QuantFlowConfigInput, type RuntimeConfig } from "../config.js";
import { DASHBOARD_HTML } from "./page.js";
import { usedNotionalUsd } from "../trading/notionalGuard.js";
import type { Engine } from "../engine.js";
import type { Fleet } from "../fleet.js";
import type { TradingLogger } from "../logger.js";
import type { Dict } from "../trading/client.js";

interface WebConsoleOptions {
  getFleet: () => Fleet;
  logger: TradingLogger;
  /** cordis config 块（基线，覆盖层叠于其上） */
  baseConfig: Record<string, unknown>;
  dataDir: string;
  host: string;
  port: number;
  token: string;
  /** 校验合并后的配置并热应用（由 index.ts 接线到 Engine.applyConfig） */
  applyConfig: (config: RuntimeConfig) => Promise<void>;
  pluginVersion: string;
}

export class WebConsole {
  private server: http.Server | null = null;
  constructor(private readonly options: WebConsoleOptions) {}

  start(): Promise<void> {
    const { host, port } = this.options;
    this.server = http.createServer((req, res) => {
      void this.handle(req, res).catch((e) => {
        this.options.logger.printError(`[看板] 请求处理异常: ${e}`);
        this.json(res, 500, { error: String(e) });
      });
    });
    return new Promise((resolve, reject) => {
      this.server!.once("error", reject);
      this.server!.listen(port, host, () => {
        this.options.logger.printInfo(`📊 看板已启动: http://${host}:${port}/`);
        resolve();
      });
    });
  }

  async stop(): Promise<void> {
    if (!this.server) return;
    await new Promise<void>((resolve) => this.server!.close(() => resolve()));
    this.server = null;
  }

  private json(res: http.ServerResponse, status: number, body: unknown): void {
    if (res.writableEnded) return;
    const payload = JSON.stringify(body, (_k, v) => (typeof v === "bigint" ? Number(v) : v));
    res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
    res.end(payload);
  }

  private authorized(req: http.IncomingMessage, url: URL): boolean {
    const token = this.options.token;
    if (!token) return true;
    const header = req.headers.authorization ?? "";
    if (header === `Bearer ${token}`) return true;
    return url.searchParams.get("token") === token;
  }

  private readBody(req: http.IncomingMessage): Promise<string> {
    return new Promise((resolve, reject) => {
      const chunks: Buffer[] = [];
      let size = 0;
      req.on("data", (c: Buffer) => {
        size += c.length;
        if (size > 1_000_000) {
          reject(new Error("请求体过大"));
          req.destroy();
          return;
        }
        chunks.push(c);
      });
      req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
      req.on("error", reject);
    });
  }

  private async handle(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
    const route = url.pathname;

    if (route === "/" || route === "/index.html") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(DASHBOARD_HTML);
      return;
    }
    if (!route.startsWith("/api/")) {
      this.json(res, 404, { error: "not found" });
      return;
    }
    if (!this.authorized(req, url)) {
      this.json(res, 401, { error: "unauthorized", hint: "需要 QUANTFLOW_WEB_TOKEN 令牌" });
      return;
    }

    const fleet = this.options.getFleet();
    // 账户维度：?account=<name> 选择账户，缺省取第一个（单账户模式即 default）
    const engine = fleet.byName(url.searchParams.get("account"));
    if (!engine) {
      return this.json(res, 404, {
        error: `账户不存在: ${url.searchParams.get("account")}`,
        accounts: fleet.engines.map((e) => e.name),
      });
    }
    const limit = Math.min(1000, Math.max(1, Number(url.searchParams.get("limit") ?? 100) || 100));

    switch (`${req.method} ${route}`) {
      case "GET /api/accounts":
        // 账户名册（无 I/O）：侧栏每次渲染都要，故与较重的 /api/fleet 分开
        return this.json(res, 200, fleet.roster());
      case "GET /api/fleet":
        // 大盘总控：全部账户的环境/策略/净值/收益（今日+累计）/持仓/净值历史
        return this.json(res, 200, await fleet.overview());
      case "GET /api/overview":
        return this.json(res, 200, await this.overview(fleet, engine));
      case "GET /api/decisions": {
        const rows = engine.logger.readRecentJsonl("decisions", limit).map((r) => ({
          ...r,
          // strategy 缺省时视为网格（历史日志里的 "perp" 来自已移除的永续策略，原样保留）
          strategy: r.strategy ?? "grid",
        })) as Dict[];
        const symbol = url.searchParams.get("symbol");
        return this.json(res, 200, symbol ? rows.filter((r) => r.symbol === symbol) : rows);
      }
      case "GET /api/trades":
        return this.json(res, 200, engine.logger.readRecentJsonl("trades", limit));
      case "GET /api/equity":
        return this.json(res, 200, engine.logger.readRecentJsonl("equity", limit));
      case "GET /api/grid": {
        if (!engine.gridStrategy) return this.json(res, 200, { enabled: false });
        const gm = engine.gridStrategy.gridManager;
        return this.json(res, 200, {
          enabled: true,
          ...(await gm.inspect(engine.gridStrategy.symbol)),
          strategy_health: engine.gridStrategy.health,
        });
      }
      case "GET /api/backtests": {
        // 回测报告由 scripts/backtest-suite.mjs 产出（可挂定时任务），看板只读展示。
        // 读不到不是错误——只说明还没跑过。
        const file = path.join(this.options.dataDir, "backtests", "latest.json");
        try {
          return this.json(res, 200, JSON.parse(fs.readFileSync(file, "utf-8")));
        } catch {
          return this.json(res, 200, {
            empty: true,
            hint: "尚无回测报告。运行 node scripts/backtest-suite.mjs --out <data_dir>/backtests 生成",
          });
        }
      }
      case "GET /api/protections":
        return this.json(res, 200, engine.protectionManager?.inspect() ?? []);
      case "GET /api/config": {
        const overrides = loadOverrides(this.options.dataDir);
        return this.json(res, 200, {
          schema: (ConfigSchema as unknown as { toJSON(): unknown }).toJSON(),
          base: this.options.baseConfig,
          overrides,
          effective: this.redact(fleet.config),
        });
      }
      case "PUT /api/config": {
        let overrides: Record<string, unknown>;
        try {
          const body = JSON.parse((await this.readBody(req)) || "{}");
          overrides = body?.overrides ?? body;
          if (!overrides || typeof overrides !== "object" || Array.isArray(overrides)) {
            throw new Error("overrides 必须是对象");
          }
        } catch (e) {
          return this.json(res, 400, { error: `请求体解析失败: ${e}` });
        }
        // 合并 → Schema 校验（失败即拒绝，绝不带病热重配）→ 落盘 → 热应用
        const merged = deepMerge(this.options.baseConfig, overrides);
        let validated: QuantFlowConfigInput;
        try {
          validated = new (ConfigSchema as never as new (v: unknown) => QuantFlowConfigInput)(merged);
          warnUnknownKeys(merged as Record<string, unknown>, (m) => this.options.logger.printWarning(m));
        } catch (e) {
          return this.json(res, 400, { error: `配置校验失败: ${e}` });
        }
        let runtime: RuntimeConfig;
        try {
          runtime = resolveRuntimeConfig(validated);
        } catch (e) {
          return this.json(res, 400, { error: `配置归一失败: ${e}` });
        }
        saveOverrides(this.options.dataDir, overrides);
        this.options.logger.printInfo("[看板] 配置覆盖已保存，开始热重配…");
        await this.options.applyConfig(runtime);
        return this.json(res, 200, { ok: true, effective: this.redact(runtime) });
      }
      case "POST /api/config/reset": {
        // 先校验/归一再清空覆盖文件：主网双重闸在归一阶段拒绝时，磁盘上的覆盖层不能已被抹掉
        const validated = new (ConfigSchema as never as new (v: unknown) => QuantFlowConfigInput)(
          this.options.baseConfig,
        );
        let runtime: RuntimeConfig;
        try {
          runtime = resolveRuntimeConfig(validated);
        } catch (e) {
          return this.json(res, 400, { error: `配置归一失败: ${e}` });
        }
        saveOverrides(this.options.dataDir, {});
        this.options.logger.printInfo("[看板] 配置覆盖已清空，回到基线配置…");
        await this.options.applyConfig(runtime);
        return this.json(res, 200, { ok: true, effective: this.redact(runtime) });
      }
      default:
        return this.json(res, 404, { error: "not found" });
    }
  }

  /** 有效配置脱敏：私钥/API Key 绝不出现在任何 HTTP 响应里。 */
  private redact(config: RuntimeConfig): Dict {
    const redactAccount = (account: EngineConfig): Dict => ({
      ...account,
      llm: { ...account.llm, api_key: account.llm.api_key ? "（已配置，来自环境变量）" : "" },
      exchange: {
        testnet: account.exchange.testnet,
        account_address: account.exchange.account_address,
        wallet: "（私钥仅存在于环境变量）",
      },
    });
    return {
      accounts: config.accounts.map(redactAccount),
      web: { ...config.web, token: config.web.token ? "（已启用令牌）" : "" },
      paths: config.paths,
    };
  }

  private async overview(fleet: Fleet, engine: Engine): Promise<Dict> {
    const cfg = engine.config;
    const [balanceInfo, positions, openOrders] = await Promise.all([
      engine.orderManager.getAvailableBalanceInfo(),
      engine.orderManager.getCurrentPositions(),
      engine.client.getOpenOrders(true),
    ]);
    // 主网名义额闸：用本次已取到的持仓/挂单算「已用 / 上限」，不额外打接口；
    // 任一查询失败即如实标注——闸门在那种情况下是 fail-closed 的
    const guard = engine.notionalGuard;
    const queryFailed = positions === null || openOrders === null;
    const mainnetGuard = guard
      ? {
          cap_usd: guard.capUsd,
          used_usd: queryFailed ? null : usedNotionalUsd(positions, openOrders),
          query_failed: queryFailed,
        }
      : null;
    return {
      plugin: { name: "dsh-plugin-quant-flow", version: this.options.pluginVersion },
      account: engine.name,
      accounts: fleet.engines.map((e) => ({
        name: e.name,
        testnet: e.config.exchange.testnet,
        running: e.isRunning,
      })),
      engine: {
        running: engine.isRunning,
        started_at: engine.startedAt,
        testnet: cfg.exchange.testnet,
        account: cfg.exchange.account_address ?? "（单钱包）",
        symbols: cfg.trading.symbols,
        grid_enabled: cfg.trading.grid_enabled,
        timeframe: cfg.trading.timeframe,
        grid_interval_minutes: cfg.grid.interval_minutes,
        llm: engine.llm.describe(),
        llm_provider: cfg.llm.provider,
        llm_in_loop: engine.llmInLoop,
        llm_usage: engine.llmUsage(),
      },
      mainnet_guard: mainnetGuard,
      balance: balanceInfo,
      positions: positions ?? [],
      positions_query_failed: positions === null,
      open_orders: openOrders ?? [],
      open_orders_query_failed: openOrders === null,
      strategies: {
        grid: engine.gridStrategy
          ? { symbol: engine.gridStrategy.symbol, health: engine.gridStrategy.health }
          : null,
      },
    };
  }
}

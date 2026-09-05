#!/usr/bin/env node
/**
 * 大盘（多账户并行）冒烟自测（手动执行，需网络——不进 vitest 套件）。
 *
 * 默认场景：**4 个地址各跑一套网格**，交易对与环境交错（ETH 测试网 / BTC 测试网 /
 * SOL 测试网 / BTC 主网），四套「地址×环境×交易对」组合同时并行；
 * 大盘总控台罗列每个账户的收益与历史。
 *
 * 账户数量不设上限——`FLEET_SIZE=N` 切换为规模模式：生成 N 个账户
 * （交易对与环境轮转交错）并行启动并逐项断言。
 *
 * 全程假私钥：每账户地址互不相同，只产生只读查询；主网实例同样只读
 * （info 接口公开），任何签名动作都会被交易所拒绝。
 *
 * 用法：npm run build && node scripts/fleet-smoke.mjs
 *       FLEET_SIZE=12 node scripts/fleet-smoke.mjs
 */
import { Context } from "@deepseek-ai/cordis";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import * as plugin from "../lib/index.js";

// 四把互不相同的假私钥 → 四个不同地址
process.env.PK_GRID = "0x" + "11".repeat(32);
process.env.PK_ALT = "0x" + "22".repeat(32);
process.env.PK_SIM = "0x" + "33".repeat(32);
process.env.PK_LIVE = "0x" + "44".repeat(32);
const home = fs.mkdtempSync(path.join(os.tmpdir(), "quantflow-fleet-"));
process.chdir(home);
console.log("工作目录:", home);

const PORT = 38121;
const SIZE = Math.max(0, Math.trunc(Number(process.env.FLEET_SIZE ?? 0)));
const ctx = new Context();

let accounts;
if (SIZE > 0) {
  // 规模模式：N 个账户，交易对/环境轮转交错（i%3：ETH 测试网 / BTC 测试网 / SOL 主网）
  const COINS = ["ETH", "BTC", "SOL"];
  accounts = Array.from({ length: SIZE }, (_, i) => {
    const env = `PK_A${i}`;
    process.env[env] = "0x" + (i + 1).toString(16).padStart(2, "0").repeat(32);
    const kind = i % 3;
    return {
      name: `acct-${String(i).padStart(3, "0")}`,
      private_key_env: env,
      testnet: kind !== 2,
      trading: { grid_enabled: true, symbols: [COINS[kind]] },
    };
  });
  console.log(`规模模式：${SIZE} 个账户`);
} else {
  accounts = [
    { name: "grid-bot", private_key_env: "PK_GRID", testnet: true,
      trading: { grid_enabled: true, symbols: ["ETH"] } },
    { name: "alt-bot", private_key_env: "PK_ALT", testnet: true,
      trading: { symbols: ["SOL"] } },
    { name: "sim", private_key_env: "PK_SIM", testnet: true },
    { name: "live", private_key_env: "PK_LIVE", testnet: false,
      trading: { max_leverage: 2 } },
  ];
}

const config = new plugin.Config({
  trading: { symbols: ["BTC"], run_immediately: false }, // 顶层=模板
  web: { port: PORT },
  fleet: { start_stagger_secs: SIZE > 0 ? 0.1 : 2 }, // 规模模式压缩错峰便于快速断言
  accounts,
});

let failed = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "✅" : "❌"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failed += 1;
};
const api = async (p) => (await fetch(`http://127.0.0.1:${PORT}${p}`)).json();

const fiber = ctx.plugin(plugin, config);
await new Promise((r) => setTimeout(r, 2000));

if (SIZE > 0) {
  // ── 规模模式断言：数量不限的实证 ──
  const t0 = Date.now();
  const f = await api("/api/fleet");
  check(`${SIZE} 个账户全部列出`, f.totals.count === SIZE, `count=${f.totals.count}`);
  check("全部运行中", f.totals.running === SIZE, `running=${f.totals.running}`);
  const addrSet = new Set(f.accounts.map((a) => a.address.toLowerCase()));
  check(`${SIZE} 个独立地址`, addrSet.size === SIZE);
  const expTest = accounts.filter((a) => a.testnet).length;
  check(
    `环境交错 ${expTest} 测试网 + ${SIZE - expTest} 主网`,
    f.totals.testnet_count === expTest && f.totals.mainnet_count === SIZE - expTest,
  );
  const gridCount = f.accounts.filter((a) => a.strategies.grid).length;
  check(`全部跑网格（${gridCount}/${SIZE}）`, gridCount === SIZE);
  const coinSet = new Set(f.accounts.map((a) => a.strategies.grid_symbol));
  check("交易对交错", coinSet.size === Math.min(3, SIZE), [...coinSet].join(","));
  check("大盘响应耗时可接受", Date.now() - t0 < 15000, `${Date.now() - t0}ms`);
  const mid = f.accounts[Math.floor(SIZE / 2)].name;
  const ov = await api(`/api/overview?account=${mid}`);
  check("任意账户维度 API 可达", ov.account === mid && ov.accounts.length === SIZE);
  check(
    "目录按账户隔离",
    f.accounts.every((a) => fs.existsSync(path.join(home, "data", "accounts", a.name))),
  );
  const t1 = Date.now();
  await fiber.dispose();
  const released = await fetch(`http://127.0.0.1:${PORT}/`).then(() => false).catch(() => true);
  check(`${SIZE} 台引擎优雅停机并释放端口`, released, `${Date.now() - t1}ms`);
  console.log(failed ? `\n${failed} 项失败` : `\n规模模式（${SIZE} 账户）：全部通过`);
  process.exit(failed ? 1 : 0);
}

// ── 1. 四账户并行启动，地址互不相同、环境交错 ──
const fleet = await api("/api/fleet");
const names = fleet.accounts.map((a) => a.name);
check("四账户并行", names.join(",") === "grid-bot,alt-bot,sim,live", names.join(","));
check("全部运行中", fleet.accounts.every((a) => a.running), `running=${fleet.totals.running}/4`);
const addrs = new Set(fleet.accounts.map((a) => a.address.toLowerCase()));
check("四个独立地址", addrs.size === 4, [...addrs].map((a) => a.slice(0, 8)).join(","));
check("环境交错 3 测试网 + 1 主网", fleet.totals.testnet_count === 3 && fleet.totals.mainnet_count === 1);
const grid = fleet.accounts.find((a) => a.name === "grid-bot");
const live = fleet.accounts.find((a) => a.name === "live");
check("交易对交错：grid-bot 跑 ETH 网格", grid.strategies.grid && grid.strategies.grid_symbol === "ETH");
check("环境交错：live 主网跑 BTC 网格", live.strategies.grid && live.strategies.grid_symbol === "BTC" && live.env === "主网");

// ── 2. 收益与历史：向各账户注入归因记录，验证逐账户核算且互不串账 ──
//    （按 TradingLogger 的落盘格式写入各账户目录——验证「文件即 API 数据源」契约）
const day = new Date().toISOString().slice(0, 10).replaceAll("-", "");
const writeJsonl = (account, kind, rows) => {
  const dir = path.join(home, "logs", "accounts", account, kind);
  fs.mkdirSync(dir, { recursive: true });
  fs.appendFileSync(
    path.join(dir, `${kind}_${day}.jsonl`),
    rows.map((r) => JSON.stringify(r)).join("\n") + "\n",
  );
};
const now = new Date().toISOString();
writeJsonl("grid-bot", "trades", [
  { timestamp: now, symbol: "ETH", action: "GRID_ROUND_TRIP", amount: 0.5, price: 100, order_id: "1", status: "FILLED", pnl: 2.5, reason: "GRID_TP" },
  { timestamp: now, symbol: "ETH", action: "GRID_NET_CLOSE", amount: 0.2, price: 101, order_id: "2", status: "FILLED", pnl: -0.5, reason: "GRID_NETTING" },
]);
writeJsonl("grid-bot", "equity", [
  { timestamp: now, equity: 100, available: 80, unrealized_pnl: 0, position_notional: 0, symbol: "ETH" },
  { timestamp: now, equity: 102, available: 82, unrealized_pnl: 0, position_notional: 0, symbol: "ETH" },
]);
writeJsonl("live", "trades", [
  { timestamp: now, symbol: "BTC", action: "CLOSE", amount: 0.1, price: 50000, order_id: "9", status: "FILLED", pnl: 10.0, reason: null },
]);

const fleet2 = await api("/api/fleet");
const g2 = fleet2.accounts.find((a) => a.name === "grid-bot");
const l2 = fleet2.accounts.find((a) => a.name === "live");
const p2 = fleet2.accounts.find((a) => a.name === "alt-bot");
check("grid-bot 今日已实现 = 2.0（2.5-0.5）", Math.abs(g2.realized_pnl_today - 2.0) < 1e-9, String(g2.realized_pnl_today));
check("grid-bot 净值历史 2 个快照", g2.equity_history.length === 2);
check("live 今日已实现 = 10.0", Math.abs(l2.realized_pnl_today - 10.0) < 1e-9);
check("alt-bot 无串账（0 收益 0 历史）", p2.realized_pnl_total === 0 && p2.equity_history.length === 0);
check("大盘汇总今日 = 12.0", Math.abs(fleet2.totals.realized_pnl_today - 12.0) < 1e-9, String(fleet2.totals.realized_pnl_today));

// ── 3. 账户维度 API：?account= 选择与隔离 ──
const gridTrades = await api("/api/trades?account=grid-bot");
const liveTrades = await api("/api/trades?account=live");
check("grid-bot 历史 2 条且不含 live 记录", gridTrades.length === 2 && gridTrades.every((t) => t.symbol === "ETH"));
check("live 历史 1 条", liveTrades.length === 1 && liveTrades[0].pnl === 10.0);
const notFound = await fetch(`http://127.0.0.1:${PORT}/api/overview?account=nope`);
check("未知账户 404 并列出可选账户", notFound.status === 404);
const ovLive = await api("/api/overview?account=live");
check("overview 按账户切换", ovLive.account === "live" && ovLive.engine.testnet === false && ovLive.accounts.length === 4);

// ── 4. 目录隔离落盘 ──
check(
  "data/logs 按账户隔离",
  ["grid-bot", "alt-bot", "sim", "live"].every((n) => fs.existsSync(path.join(home, "data", "accounts", n))),
);

// ── 5. 热重配：改 sim 的杠杆 → 大盘整体重建仍 4 账户并行 ──
const put = await fetch(`http://127.0.0.1:${PORT}/api/config`, {
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ overrides: { accounts: config.accounts.map((a) =>
    a.name === "sim" ? { ...a, trading: { ...a.trading, max_leverage: 4 } } : a) } }),
});
check("热重配请求成功", put.status === 200);
await new Promise((r) => setTimeout(r, 1500));
const fleet3 = await api("/api/fleet");
check("热重配后仍 4 账户全运行", fleet3.totals.count === 4 && fleet3.totals.running === 4);

// ── 6. 优雅停机 ──
const t0 = Date.now();
await fiber.dispose();
const released = await fetch(`http://127.0.0.1:${PORT}/`).then(() => false).catch(() => true);
check("四引擎优雅停机并释放端口", released, `${Date.now() - t0}ms`);

console.log(failed ? `\n${failed} 项失败` : "\n大盘多账户并行冒烟：全部通过");
process.exit(failed ? 1 : 0);

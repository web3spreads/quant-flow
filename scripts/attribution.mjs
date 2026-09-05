#!/usr/bin/env node
/**
 * 盈亏归因：把账户的链上成交（Hyperliquid userFills，公开 info 接口）拆成
 * maker/taker 费用、已实现盈亏（按平仓方向）、风控强平、资金费，按日汇总。
 *
 * 只读、不需要私钥；地址来自参数或环境变量 HYPERLIQUID_ACCOUNT_ADDRESS。
 * 可选读取本地 data/grid_state.json 的 netting_attribution.forced_oids 识别强平腿。
 *
 * 用法：
 *   node scripts/attribution.mjs --address 0x... [--testnet] [--days 7] [--state data/grid_state.json]
 *   HYPERLIQUID_ACCOUNT_ADDRESS=0x... node scripts/attribution.mjs --testnet
 */
import fs from "node:fs";

const argv = process.argv.slice(2);
const args = {};
for (let i = 0; i < argv.length; i++) {
  if (!argv[i].startsWith("--")) continue;
  const next = argv[i + 1];
  if (next === undefined || next.startsWith("--")) args[argv[i].slice(2)] = "true";
  else {
    args[argv[i].slice(2)] = next;
    i += 1;
  }
}
const address = String(args.address ?? process.env.HYPERLIQUID_ACCOUNT_ADDRESS ?? "").trim();
if (!/^0x[0-9a-fA-F]{40}$/.test(address)) {
  console.error("需要 --address 0x...（或环境变量 HYPERLIQUID_ACCOUNT_ADDRESS）");
  process.exit(1);
}
const api = args.testnet === "true" ? "https://api.hyperliquid-testnet.xyz/info" : "https://api.hyperliquid.xyz/info";
const days = Number(args.days ?? 7);
const since = Date.now() - days * 86400e3;

async function post(body) {
  const resp = await fetch(api, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

let forcedOids = new Set();
if (args.state && fs.existsSync(String(args.state))) {
  try {
    const state = JSON.parse(fs.readFileSync(String(args.state), "utf-8"));
    for (const bucket of Object.values(state.netting_attribution ?? {})) {
      for (const oid of bucket.forced_oids ?? []) forcedOids.add(String(oid));
    }
  } catch {
    /* 状态文件不可读则不区分强平 */
  }
}

const fills = (await post({ type: "userFillsByTime", user: address, startTime: since })) ?? [];
const funding = (await post({ type: "userFunding", user: address, startTime: since })) ?? [];

const byDay = {};
const day = (t) => new Date(t).toISOString().slice(0, 10);
const bucket = (d) => (byDay[d] ??= { makerFee: 0, takerFee: 0, makerFills: 0, takerFills: 0, volume: 0, closedPnl: 0, forcedPnl: 0, closes: 0, funding: 0 });
for (const f of fills) {
  const b = bucket(day(Number(f.time)));
  const fee = Number(f.fee ?? 0);
  const notional = Number(f.px) * Number(f.sz);
  if (f.crossed) {
    b.takerFee += fee;
    b.takerFills += 1;
  } else {
    b.makerFee += fee;
    b.makerFills += 1;
  }
  b.volume += notional;
  const pnl = Number(f.closedPnl ?? 0);
  if (pnl !== 0) {
    b.closes += 1;
    b.closedPnl += pnl;
    if (forcedOids.has(String(f.oid))) b.forcedPnl += pnl;
  }
}
for (const row of funding) {
  const delta = row.delta ?? row;
  const b = bucket(day(Number(row.time)));
  b.funding += Number(delta.usdc ?? 0);
}

const f2 = (n) => (n >= 0 ? "+" : "") + n.toFixed(2);
// 不打印地址：仓库红线要求钱包地址不得进入日志/文档/模型输出，而本脚本的输出
// 经常被贴进工单或对话。需要确认查的是哪个账户时用 --show-address。
const who = args["show-address"] === "true" ? address : "（地址已隐去，--show-address 可显示）";
console.log(`账户 ${who}  ${args.testnet === "true" ? "测试网" : "主网"}  最近 ${days} 天  成交 ${fills.length} 笔  资金费 ${funding.length} 条\n`);
console.log("日期        成交(m/t)  taker占比   成交额     maker费   taker费   已实现    其中强平   资金费     净额");
const total = { makerFee: 0, takerFee: 0, makerFills: 0, takerFills: 0, volume: 0, closedPnl: 0, forcedPnl: 0, funding: 0 };
for (const d of Object.keys(byDay).sort()) {
  const b = byDay[d];
  const fillsN = b.makerFills + b.takerFills;
  const net = b.closedPnl - b.makerFee - b.takerFee + b.funding;
  console.log(
    `${d}  ${String(b.makerFills).padStart(4)}/${String(b.takerFills).padEnd(4)} ${(fillsN ? (100 * b.takerFills) / fillsN : 0).toFixed(1).padStart(7)}%  ` +
      `${b.volume.toFixed(0).padStart(8)}  ${b.makerFee.toFixed(3).padStart(8)}  ${b.takerFee.toFixed(3).padStart(8)}  ` +
      `${f2(b.closedPnl).padStart(8)}  ${f2(b.forcedPnl).padStart(8)}  ${f2(b.funding).padStart(8)}  ${f2(net).padStart(8)}`,
  );
  for (const k of Object.keys(total)) total[k] += b[k];
}
const fillsN = total.makerFills + total.takerFills;
console.log(
  `\n合计: 成交 ${fillsN}（taker ${fillsN ? ((100 * total.takerFills) / fillsN).toFixed(1) : 0}%）  成交额 $${total.volume.toFixed(0)}  ` +
    `费用 maker $${total.makerFee.toFixed(2)} / taker $${total.takerFee.toFixed(2)}  已实现 ${f2(total.closedPnl)}（强平 ${f2(total.forcedPnl)}）  资金费 ${f2(total.funding)}  ` +
    `净额 ${f2(total.closedPnl - total.makerFee - total.takerFee + total.funding)}`,
);

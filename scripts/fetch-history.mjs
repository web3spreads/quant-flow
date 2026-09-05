#!/usr/bin/env node
/**
 * 拉取 Hyperliquid 公开历史数据（K 线 + 资金费率）供回测使用。
 *
 * 只走 info 公开接口，不需要私钥。输出 JSONL 到 data/history/：
 *   data/history/<COIN>_<interval>.jsonl   每行 {t,o,h,l,c,v}（t=起始毫秒，升序去重）
 *   data/history/<COIN>_funding.jsonl      每行 {time,fundingRate,premium}
 *
 * 用法：
 *   node scripts/fetch-history.mjs --coin BTC --interval 1m --days 60
 *   node scripts/fetch-history.mjs --coin ETH --interval 5m --days 180 --out data/history
 *
 * candleSnapshot 单次最多返回约 5000 根，脚本按窗口分页并断点续传（已有文件
 * 只补末尾之后的数据）。
 */
import fs from "node:fs";
import path from "node:path";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => {
    if (cur.startsWith("--")) acc.push([cur.slice(2), arr[i + 1] && !arr[i + 1].startsWith("--") ? arr[i + 1] : "true"]);
    return acc;
  }, []),
);
const coin = String(args.coin ?? "BTC").toUpperCase();
const interval = String(args.interval ?? "1m");
const days = Number(args.days ?? 60);
const outDir = String(args.out ?? "data/history");
const api = args.testnet === "true" ? "https://api.hyperliquid-testnet.xyz/info" : "https://api.hyperliquid.xyz/info";

const INTERVAL_MS = { "1m": 60e3, "3m": 180e3, "5m": 300e3, "15m": 900e3, "30m": 1800e3, "1h": 3600e3, "2h": 7200e3, "4h": 14400e3, "8h": 28800e3, "12h": 43200e3, "1d": 86400e3 };
const stepMs = INTERVAL_MS[interval];
if (!stepMs) throw new Error(`不支持的周期: ${interval}`);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function post(body, attempt = 1) {
  const resp = await fetch(api, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (resp.status === 429 && attempt <= 5) {
    await sleep(1000 * attempt);
    return post(body, attempt + 1);
  }
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${(await resp.text()).slice(0, 200)}`);
  return resp.json();
}

fs.mkdirSync(outDir, { recursive: true });

function loadExisting(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf-8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

async function fetchCandles() {
  const file = path.join(outDir, `${coin}_${interval}.jsonl`);
  const existing = loadExisting(file);
  const now = Date.now();
  let start = existing.length ? existing[existing.length - 1].t + stepMs : now - days * 86400e3;
  const seen = new Set(existing.map((r) => r.t));
  const rows = [...existing];
  // 每页约 5000 根
  const pageMs = stepMs * 4900;
  let pages = 0;
  while (start < now) {
    const end = Math.min(start + pageMs, now);
    const data = await post({ type: "candleSnapshot", req: { coin, interval, startTime: start, endTime: end } });
    pages += 1;
    let added = 0;
    for (const c of data ?? []) {
      const t = Number(c.t);
      if (!Number.isFinite(t) || seen.has(t)) continue;
      seen.add(t);
      rows.push({ t, o: Number(c.o), h: Number(c.h), l: Number(c.l), c: Number(c.c), v: Number(c.v) });
      added += 1;
    }
    process.stderr.write(`[${coin} ${interval}] 第 ${pages} 页 ${new Date(start).toISOString().slice(0, 16)} → +${added} 根（累计 ${rows.length}）\n`);
    // 交易所只保留每周期最近约 5000 根：早于保留窗口的页返回空，跳过继续往后翻
    start = end;
    await sleep(250);
  }
  rows.sort((a, b) => a.t - b.t);
  fs.writeFileSync(file, rows.map((r) => JSON.stringify(r)).join("\n") + "\n");
  process.stderr.write(`写入 ${file}：${rows.length} 根\n`);
}

async function fetchFunding() {
  const file = path.join(outDir, `${coin}_funding.jsonl`);
  const existing = loadExisting(file);
  const now = Date.now();
  let start = existing.length ? existing[existing.length - 1].time + 1 : now - days * 86400e3;
  const seen = new Set(existing.map((r) => r.time));
  const rows = [...existing];
  while (start < now) {
    const data = await post({ type: "fundingHistory", coin, startTime: start, endTime: now });
    if (!data?.length) break;
    let added = 0;
    let maxT = start;
    for (const f of data) {
      const t = Number(f.time);
      maxT = Math.max(maxT, t);
      if (seen.has(t)) continue;
      seen.add(t);
      rows.push({ time: t, fundingRate: Number(f.fundingRate), premium: Number(f.premium) });
      added += 1;
    }
    process.stderr.write(`[${coin} funding] ${new Date(start).toISOString().slice(0, 16)} → +${added} 条（累计 ${rows.length}）\n`);
    if (maxT <= start) break;
    start = maxT + 1;
    await sleep(250);
  }
  rows.sort((a, b) => a.time - b.time);
  fs.writeFileSync(file, rows.map((r) => JSON.stringify(r)).join("\n") + "\n");
  process.stderr.write(`写入 ${file}：${rows.length} 条\n`);
}

/** 抓一次全市场元数据（szDecimals / maxLeverage），供回测按真实精度撮合。 */
async function fetchMeta() {
  const file = path.join(outDir, "assets.json");
  const meta = await post({ type: "meta" });
  const out = {};
  for (const a of meta?.universe ?? []) {
    if (!a?.name) continue;
    const maxLeverage = Number(a.maxLeverage ?? 20);
    out[a.name] = {
      szDecimals: Number(a.szDecimals ?? 3),
      maxLeverage,
      // HL 维持保证金率约为 1/(2 × 最大杠杆)
      maintenanceRate: 1 / (2 * Math.max(1, maxLeverage)),
    };
  }
  fs.writeFileSync(file, JSON.stringify(out, null, 2));
  process.stderr.write(`写入 ${file}：${Object.keys(out).length} 个交易对\n`);
}

if (args.meta === "true") {
  await fetchMeta();
} else {
  await fetchCandles();
  if (args.funding !== "false") await fetchFunding();
}

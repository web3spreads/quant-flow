#!/usr/bin/env node
/**
 * 网格回测 CLI：用生产同一套引擎跑历史 K 线，输出费用/库存/强平/收益分解。
 *
 * 需先 `npm run build`（导入 lib/）。数据来自 scripts/fetch-history.mjs。
 *
 * 用法：
 *   node scripts/backtest-grid.mjs --symbol BTC --interval 5m                 # 默认参数
 *   node scripts/backtest-grid.mjs --symbol BTC --interval 5m --preset triggers # 触发单退出对照组
 *   node scripts/backtest-grid.mjs --symbol BTC --interval 15m --compare      # 新旧并排
 *   node scripts/backtest-grid.mjs --symbol BTC --config '{"grid":{"width_min_pct":0.01}}'
 *   node scripts/backtest-grid.mjs --symbol BTC --sweep grid.inventory_cap_ratio=1,1.5,2,3
 *   node scripts/backtest-grid.mjs ... --json out.json   # 完整结果（含净值曲线）落盘
 *
 * 可选：--equity 1000 --warmup 300 --grid-num 8 --maker 0.00015 --taker 0.00045 --no-funding
 *
 * LLM 后端固定为规则后端（每周期都说 UPDATE_GRID，由重建闸门决定真实重建频率）。
 */
import fs from "node:fs";
import path from "node:path";
import { TRIGGER_EXIT_PRESET, formatReport, loadBars, loadFunding, runBacktest } from "../lib/index.js";

const argv = process.argv.slice(2);
const args = {};
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (!a.startsWith("--")) continue;
  const key = a.slice(2);
  const next = argv[i + 1];
  if (next === undefined || next.startsWith("--")) args[key] = "true";
  else {
    args[key] = next;
    i += 1;
  }
}

const symbol = String(args.symbol ?? "BTC").toUpperCase();
const interval = String(args.interval ?? "5m");
const dir = String(args.dir ?? "data/history");
const file = String(args.file ?? path.join(dir, `${symbol}_${interval}.jsonl`));
if (!fs.existsSync(file)) {
  console.error(`找不到数据文件 ${file}，先运行: node scripts/fetch-history.mjs --coin ${symbol} --interval ${interval}`);
  process.exit(1);
}
const bars = loadBars(file);
const fundingFile = path.join(dir, `${symbol}_funding.jsonl`);
const funding = args["no-funding"] === "true" || !fs.existsSync(fundingFile) ? [] : loadFunding(fundingFile);
const initialEquity = Number(args.equity ?? 1000);
const warmupBars = Number(args.warmup ?? 300);
const gridNum = Number(args["grid-num"] ?? 8);
const feeRates = { makerRate: Number(args.maker ?? 0.00015), takerRate: Number(args.taker ?? 0.00045) };
const userConfig = args.config ? JSON.parse(String(args.config)) : {};
const assetsFile = path.join(dir, "assets.json");
const assets = fs.existsSync(assetsFile) ? JSON.parse(fs.readFileSync(assetsFile, "utf-8")) : undefined;

function deepMerge(base, patch) {
  const out = { ...base };
  for (const [k, v] of Object.entries(patch ?? {})) {
    out[k] = v && typeof v === "object" && !Array.isArray(v) && out[k] && typeof out[k] === "object" && !Array.isArray(out[k])
      ? deepMerge(out[k], v)
      : v;
  }
  return out;
}

function setPath(obj, dotted, value) {
  const keys = dotted.split(".");
  const out = JSON.parse(JSON.stringify(obj));
  let cur = out;
  for (const k of keys.slice(0, -1)) cur = cur[k] = cur[k] ?? {};
  cur[keys[keys.length - 1]] = value;
  return out;
}

async function run(title, config) {
  const t0 = Date.now();
  const result = await runBacktest({
    symbol, bars, funding, config, initialEquity, feeRates, warmupBars, gridNum, assets,
    keepArtifacts: args.keep === "true",
  });
  console.log(formatReport(result, `${title}  [${((Date.now() - t0) / 1000).toFixed(1)}s]`));
  if (result.artifactsDir) console.log(`日志/状态: ${result.artifactsDir}`);
  console.log("");
  return result;
}

console.log(`数据: ${file}（${bars.length} 根，${new Date(bars[0].t).toISOString().slice(0, 10)} → ${new Date(bars[bars.length - 1].t).toISOString().slice(0, 10)}）资金费率 ${funding.length} 条\n`);

const results = {};
const presetName = String(args.preset ?? "current");
if (args.sweep) {
  const [key, list] = String(args.sweep).split("=");
  for (const raw of list.split(",")) {
    const value = raw === "true" ? true : raw === "false" ? false : raw === "null" ? null : Number.isFinite(Number(raw)) ? Number(raw) : raw;
    results[`${key}=${raw}`] = await run(`${key}=${raw}`, setPath(deepMerge(presetName === "triggers" ? TRIGGER_EXIT_PRESET : {}, userConfig), key, value));
  }
} else if (args.compare === "true") {
  results.triggers = await run("触发单退出对照组（层级触发单 / Gtc / 4h 时限 / 固定金额预算）", deepMerge(TRIGGER_EXIT_PRESET, userConfig));
  results.current = await run("当前默认（post-only 批量 / 无层级触发单 / 按权益预算 / 自动库存上限）", userConfig);
} else {
  results[presetName] = await run(presetName === "triggers" ? "触发单退出对照组" : "当前默认", deepMerge(presetName === "triggers" ? TRIGGER_EXIT_PRESET : {}, userConfig));
}

if (args.json) {
  fs.writeFileSync(String(args.json), JSON.stringify(results, null, 2));
  console.log(`已写入 ${args.json}`);
}

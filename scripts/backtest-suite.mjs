#!/usr/bin/env node
/**
 * 回测套件：把网格策略在一组标的×周期上整批跑一遍，
 * 结果落盘成 JSON 供看板读取。设计成可以挂 cron / systemd timer 定期执行。
 *
 * 需先 `npm run build`，数据来自 scripts/fetch-history.mjs。
 *
 * 用法：
 *   node scripts/backtest-suite.mjs                                  # 默认套件
 *   node scripts/backtest-suite.mjs --symbols BTC,ETH --intervals 15m,1h
 *   node scripts/backtest-suite.mjs --out /home/ubuntu/quantflow/data/backtests
 *
 * 输出：<out>/latest.json（看板读这个）与 <out>/<时间戳>.json（历史留档）。
 */
import fs from "node:fs";
import path from "node:path";
import { loadBars, loadFunding, runBacktest } from "../lib/index.js";

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

const dataDir = String(args.dir ?? "data/history");
const outDir = String(args.out ?? "data/backtests");
const symbols = String(args.symbols ?? "BTC,ETH,SOL,BNB").split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
const intervals = String(args.intervals ?? "15m,1h").split(",").map((s) => s.trim()).filter(Boolean);
const initialEquity = Number(args.equity ?? 1000);
const feeRates = { makerRate: Number(args.maker ?? 0.00015), takerRate: Number(args.taker ?? 0.00045) };
const config = args.config ? JSON.parse(String(args.config)) : {};
const keepCurves = args["no-curves"] !== "true";
const fillOnTouch = args["fill-on-touch"] === "true";

const assetsFile = path.join(dataDir, "assets.json");
const assets = fs.existsSync(assetsFile) ? JSON.parse(fs.readFileSync(assetsFile, "utf-8")) : undefined;

/** 净值曲线按天下采样：看板画图够用，文件不会膨胀。 */
function daily(curve) {
  const out = [];
  let lastDay = "";
  for (const p of curve) {
    const d = new Date(p.t).toISOString().slice(0, 10);
    if (d !== lastDay) {
      out.push({ t: p.t, equity: Math.round(p.equity * 100) / 100 });
      lastDay = d;
    }
  }
  return out;
}

const runs = [];
const started = Date.now();

/** runBacktest 结果 → 看板用的 run 记录（矩阵跑出来的和外部合并进来的共用同一形状）。 */
function toRun(symbol, interval, label, r, elapsedMs, extra = {}) {
  return {
    symbol, interval, strategy: label,
    days: Math.round(r.days * 10) / 10,
    returnPct: r.returnPct,
    returnPctPerDay: r.returnPctPerDay,
    maxDrawdownPct: r.maxDrawdownPct,
    benchmarkPct: r.benchmarkPct,
    realizedPnl: r.realizedPnl,
    fees: r.fees,
    fundingPaid: r.fundingPaid,
    fills: r.fills,
    liquidations: r.liquidations,
    maxInventoryNotional: r.maxInventoryNotional,
    decisions: r.decisions,
    equityCurve: keepCurves ? daily(r.equityCurve) : [],
    elapsedMs,
    ...extra,
  };
}

for (const symbol of symbols) {
  for (const interval of intervals) {
    const file = path.join(dataDir, `${symbol}_${interval}.jsonl`);
    if (!fs.existsSync(file)) {
      process.stderr.write(`跳过 ${symbol} ${interval}：无数据文件\n`);
      continue;
    }
    const bars = loadBars(file);
    const fundingFile = path.join(dataDir, `${symbol}_funding.jsonl`);
    const funding = fs.existsSync(fundingFile) ? loadFunding(fundingFile) : [];

    const label = "grid";
    const t0 = Date.now();
    try {
      const r = await runBacktest({ symbol, bars, funding, config, initialEquity, feeRates, assets, fillOnTouch });
      runs.push(toRun(symbol, interval, label, r, Date.now() - t0));
      process.stderr.write(
        `${symbol} ${interval} ${label}: ${r.returnPct >= 0 ? "+" : ""}${r.returnPct.toFixed(2)}% ` +
        `(回撤 ${r.maxDrawdownPct.toFixed(2)}%, taker ${(r.fills.takerRatio * 100).toFixed(0)}%) ` +
        `[${((Date.now() - t0) / 1000).toFixed(1)}s]\n`,
      );
    } catch (e) {
      process.stderr.write(`${symbol} ${interval} ${label}: 失败 ${e}\n`);
      runs.push({ symbol, interval, strategy: label, error: String(e) });
    }
  }
}

/**
 * 按策略汇总：均值/中位数/胜率——单个样本没有意义，看的是分布。
 * 两条基准同表：「不交易」恒为 0（t 值就是对它的检验），「买入持有」取同区间标的涨跌均值。
 */
function summarize(label) {
  const ok = runs.filter((r) => r.strategy === label && r.error === undefined);
  const xs = ok.map((r) => r.returnPct);
  if (!xs.length) return null;
  const sorted = [...xs].sort((a, b) => a - b);
  const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
  const sd = Math.sqrt(xs.reduce((a, b) => a + (b - mean) ** 2, 0) / xs.length);
  const fees = ok.map((r) => r.fees.pctOfInitial);
  const bench = ok.map((r) => Number(r.benchmarkPct) || 0);
  const meanBench = bench.reduce((a, b) => a + b, 0) / bench.length;
  return {
    n: xs.length,
    meanPct: mean,
    medianPct: sorted[Math.floor(sorted.length / 2)],
    sdPct: sd,
    winRate: xs.filter((x) => x > 0).length / xs.length,
    worstPct: sorted[0],
    bestPct: sorted[sorted.length - 1],
    meanFeePctOfEquity: fees.reduce((a, b) => a + b, 0) / (fees.length || 1),
    // 均值是否显著异于零：|t| < 2 就是「和不交易没区别」
    tStat: sd > 0 ? mean / (sd / Math.sqrt(xs.length)) : 0,
    noTradePct: 0,
    meanBenchmarkPct: meanBench,
    vsBuyHoldPct: mean - meanBench,
    beatBuyHoldRate: ok.filter((r) => r.returnPct > (Number(r.benchmarkPct) || 0)).length / ok.length,
  };
}

const report = {
  generatedAt: new Date().toISOString(),
  elapsedMs: Date.now() - started,
  params: { symbols, intervals, initialEquity, feeRates, config, fillOnTouch },
  runs,
  summary: Object.fromEntries(
    [...new Set(runs.map((r) => r.strategy))].map((s) => [s, summarize(s)]).filter(([, v]) => v),
  ),
};

fs.mkdirSync(outDir, { recursive: true });
const stamp = report.generatedAt.replace(/[:.]/g, "-");
fs.writeFileSync(path.join(outDir, `${stamp}.json`), JSON.stringify(report, null, 2));
fs.writeFileSync(path.join(outDir, "latest.json"), JSON.stringify(report, null, 2));

// 只保留最近 30 份历史留档
const olds = fs.readdirSync(outDir).filter((f) => f.endsWith(".json") && f !== "latest.json").sort();
for (const f of olds.slice(0, Math.max(0, olds.length - 30))) fs.unlinkSync(path.join(outDir, f));

console.log(`\n${"".padEnd(64, "=")}`);
console.log(`回测套件完成：${runs.length} 次运行，耗时 ${(report.elapsedMs / 1000).toFixed(0)}s`);
console.log("".padEnd(64, "="));
console.log(`\n${"样本".padEnd(16)}${"策略".padEnd(12)}${"收益".padStart(9)}${"回撤".padStart(9)}${"标的".padStart(9)}${"费用".padStart(8)}${"taker".padStart(7)}`);
for (const r of runs) {
  if (r.error) {
    console.log(`${(r.symbol + " " + r.interval).padEnd(16)}${r.strategy.padEnd(12)}  失败`);
    continue;
  }
  console.log(
    `${(r.symbol + " " + r.interval).padEnd(16)}${r.strategy.padEnd(12)}` +
    `${(r.returnPct >= 0 ? "+" : "") + r.returnPct.toFixed(2) + "%"}`.padStart(9) +
    `${r.maxDrawdownPct.toFixed(2) + "%"}`.padStart(9) +
    `${(r.benchmarkPct >= 0 ? "+" : "") + r.benchmarkPct.toFixed(1) + "%"}`.padStart(9) +
    `${r.fees.pctOfInitial.toFixed(2) + "%"}`.padStart(8) +
    `${(r.fills.takerRatio * 100).toFixed(0) + "%"}`.padStart(7),
  );
}
console.log(`\n${"策略".padEnd(12)}${"样本数".padStart(7)}${"均值".padStart(9)}${"中位数".padStart(9)}${"标准差".padStart(9)}${"胜率".padStart(8)}${"t 值".padStart(8)}${"平均费用".padStart(10)}${"买入持有".padStart(10)}${"相对持有".padStart(10)}`);
for (const [label, s] of Object.entries(report.summary)) {
  console.log(
    label.padEnd(12) +
    String(s.n).padStart(7) +
    `${(s.meanPct >= 0 ? "+" : "") + s.meanPct.toFixed(2) + "%"}`.padStart(9) +
    `${(s.medianPct >= 0 ? "+" : "") + s.medianPct.toFixed(2) + "%"}`.padStart(9) +
    `${s.sdPct.toFixed(2) + "%"}`.padStart(9) +
    `${(s.winRate * 100).toFixed(0) + "%"}`.padStart(8) +
    `${s.tStat.toFixed(2)}`.padStart(8) +
    `${s.meanFeePctOfEquity.toFixed(2) + "%"}`.padStart(10) +
    `${(s.meanBenchmarkPct >= 0 ? "+" : "") + s.meanBenchmarkPct.toFixed(2) + "%"}`.padStart(10) +
    `${(s.vsBuyHoldPct >= 0 ? "+" : "") + s.vsBuyHoldPct.toFixed(2) + "%"}`.padStart(10),
  );
}
console.log(`\n|t| < 2 表示该策略的平均收益与「不交易」（0）在统计上无法区分；「相对持有」= 均值 − 同期买入持有均值。`);
console.log(`已写入 ${path.join(outDir, "latest.json")}`);

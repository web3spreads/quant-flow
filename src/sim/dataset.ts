/**
 * 回测数据集：历史 K 线 / 资金费率的加载、重采样与合成序列。
 *
 * 文件格式与 scripts/fetch-history.mjs 的输出一致（JSONL，每行一根 K 线
 * `{t,o,h,l,c,v}`，t=起始毫秒）。Hyperliquid 每个周期只保留最近约 5000 根，
 * 高周期由基础周期重采样得到，保证多周期趋势标签与实盘同源。
 */

import fs from "node:fs";

export interface Bar {
  /** 起始时间（UTC 毫秒） */
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface FundingRow {
  /** 结算时间（UTC 毫秒，整点） */
  time: number;
  /** 小时费率（小数，正=多头付给空头） */
  fundingRate: number;
  premium?: number;
}

export const INTERVAL_MS: Record<string, number> = {
  "1m": 60e3, "3m": 180e3, "5m": 300e3, "15m": 900e3, "30m": 1800e3,
  "1h": 3600e3, "2h": 7200e3, "4h": 14400e3, "8h": 28800e3, "12h": 43200e3, "1d": 86400e3,
};

function readJsonl<T>(file: string): T[] {
  const out: T[] = [];
  for (const line of fs.readFileSync(file, "utf-8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      out.push(JSON.parse(trimmed) as T);
    } catch {
      // 追加型文件的个别损坏行跳过
    }
  }
  return out;
}

/** 加载 K 线（升序、按 t 去重、丢弃非数值行）。 */
export function loadBars(file: string): Bar[] {
  const seen = new Set<number>();
  const rows: Bar[] = [];
  for (const raw of readJsonl<Record<string, unknown>>(file)) {
    const bar: Bar = {
      t: Number(raw.t), o: Number(raw.o), h: Number(raw.h), l: Number(raw.l), c: Number(raw.c), v: Number(raw.v ?? 0),
    };
    if (![bar.t, bar.o, bar.h, bar.l, bar.c].every(Number.isFinite) || seen.has(bar.t)) continue;
    seen.add(bar.t);
    rows.push(bar);
  }
  return rows.sort((a, b) => a.t - b.t);
}

/** 加载资金费率序列（升序）。 */
export function loadFunding(file: string): FundingRow[] {
  return readJsonl<Record<string, unknown>>(file)
    .map((r) => ({ time: Number(r.time), fundingRate: Number(r.fundingRate), premium: Number(r.premium ?? 0) }))
    .filter((r) => Number.isFinite(r.time) && Number.isFinite(r.fundingRate))
    .sort((a, b) => a.time - b.time);
}

/** 推断基础周期（相邻 K 线时间差的中位数）。 */
export function inferIntervalMs(bars: Bar[]): number {
  if (bars.length < 2) return 60e3;
  const diffs = bars.slice(1).map((b, i) => b.t - bars[i].t).filter((d) => d > 0).sort((a, b) => a - b);
  return diffs[Math.floor(diffs.length / 2)] ?? 60e3;
}

/**
 * 重采样到更高周期（按 UTC 整数倍对齐：1h 在 :00 起始，1d 在 00:00）。
 * 目标周期小于基础周期时返回空数组（无法凭空造出更细的数据）。
 * 只输出「桶内至少有一根基础 K 线」的桶；桶是否收盘由调用方按 t+周期 判断。
 */
export function resampleBars(bars: Bar[], targetMs: number, baseMs?: number): Bar[] {
  const base = baseMs ?? inferIntervalMs(bars);
  if (targetMs < base) return [];
  if (targetMs === base) return bars;
  const out: Bar[] = [];
  let current: Bar | null = null;
  for (const bar of bars) {
    const bucket = Math.floor(bar.t / targetMs) * targetMs;
    if (current && current.t === bucket) {
      current.h = Math.max(current.h, bar.h);
      current.l = Math.min(current.l, bar.l);
      current.c = bar.c;
      current.v += bar.v;
    } else {
      if (current) out.push(current);
      current = { t: bucket, o: bar.o, h: bar.h, l: bar.l, c: bar.c, v: bar.v };
    }
  }
  if (current) out.push(current);
  return out;
}

/** 确定性伪随机（mulberry32），合成序列可复现。 */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface SyntheticOptions {
  /** 起始时间（UTC 毫秒，默认 2026-01-01） */
  start?: number;
  intervalMs?: number;
  count: number;
  price0?: number;
  /** sine=纯震荡（网格理想市况）；trend=单边漂移；gbm=几何布朗运动；sine+gbm=震荡叠噪声 */
  kind?: "sine" | "trend" | "gbm" | "sine+gbm";
  /** 正弦振幅（相对价格，如 0.02=±2%） */
  amplitudePct?: number;
  /** 正弦周期（根数） */
  periodBars?: number;
  /** 每根漂移（相对，如 0.0005） */
  driftPctPerBar?: number;
  /** 每根波动（相对，如 0.002） */
  volPctPerBar?: number;
  seed?: number;
}

/** 合成 K 线：每根的 high/low 由相邻收盘价加少量随机扩展得到，保证 l ≤ min(o,c) ≤ max(o,c) ≤ h。 */
export function syntheticBars(options: SyntheticOptions): Bar[] {
  const start = options.start ?? Date.parse("2026-01-01T00:00:00Z");
  const intervalMs = options.intervalMs ?? 300e3;
  const price0 = options.price0 ?? 100_000;
  const kind = options.kind ?? "sine";
  const amp = options.amplitudePct ?? 0.02;
  const period = Math.max(4, options.periodBars ?? 72);
  const drift = options.driftPctPerBar ?? 0;
  const vol = options.volPctPerBar ?? 0.002;
  const rand = mulberry32(options.seed ?? 42);
  const gauss = () => {
    // Box-Muller
    const u = Math.max(rand(), 1e-12);
    const v = rand();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  };

  const closes: number[] = [];
  let level = price0;
  for (let i = 0; i < options.count; i++) {
    let px: number;
    if (kind === "sine") {
      px = price0 * (1 + amp * Math.sin((2 * Math.PI * i) / period));
    } else if (kind === "trend") {
      level *= 1 + drift + vol * gauss() * 0.25;
      px = level;
    } else if (kind === "gbm") {
      level *= 1 + drift + vol * gauss();
      px = level;
    } else {
      level *= 1 + vol * gauss() * 0.5;
      px = level * (1 + amp * Math.sin((2 * Math.PI * i) / period));
    }
    closes.push(px);
  }

  const bars: Bar[] = [];
  for (let i = 0; i < closes.length; i++) {
    const o = i === 0 ? closes[0] : closes[i - 1];
    const c = closes[i];
    const wick = Math.abs(c) * vol * 0.6 * rand();
    const h = Math.max(o, c) + wick;
    const l = Math.min(o, c) - wick;
    bars.push({ t: start + i * intervalMs, o, h, l, c, v: 1000 + 500 * rand() });
  }
  return bars;
}

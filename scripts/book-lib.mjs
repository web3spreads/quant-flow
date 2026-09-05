/**
 * 盘口录制器的纯逻辑库：日统计（缺口/延迟/覆盖秒数）、gzip 完整性校验、清单生成、
 * 磁盘水位判定。record-book.mjs 与 book-verify.mjs 只做接线（不放在 lib/ 目录下：.gitignore 的 lib/ 规则会把它忽略掉），这里的函数全部可被
 * vitest 直接 import 测试（无网络、无全局副作用）。
 *
 * 清单（manifest.json，每 <COIN>/<UTC 日> 一份）是数据资产的所有权凭证：
 * 备份端靠它核对 sha256 与行数，研究管线靠它的 coverage 决定哪些日子纳入样本。
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { Transform, Writable } from "node:stream";
import { pipeline } from "node:stream/promises";

/** 频道名 → 落盘文件名（无扩展名） */
export const CHANNEL_FILE = { l2Book: "l2book", l2Full: "l2full", trades: "trades", bbo: "bbo", activeAssetCtx: "ctx" };
export const FILE_NAMES = Object.values(CHANNEL_FILE);
export const MANIFEST_NAME = "manifest.json";
export const MANIFEST_VERSION = 1;

export const utcDay = (ms) => new Date(ms).toISOString().slice(0, 10);
export const dayStartMs = (day) => Date.parse(`${day}T00:00:00Z`);

// ── 蓄水池抽样（Algorithm R）：固定内存估计分位数 ─────────────────────────

export class Reservoir {
  constructor(size = 20_000) {
    this.size = size;
    this.samples = [];
    this.count = 0;
  }
  push(value) {
    if (!Number.isFinite(value)) return;
    this.count += 1;
    if (this.samples.length < this.size) {
      this.samples.push(value);
      return;
    }
    const j = Math.floor(Math.random() * this.count);
    if (j < this.size) this.samples[j] = value;
  }
  /** 分位数（最近秩法）；无样本返回 null */
  percentiles(ps = [50, 90, 99]) {
    if (!this.samples.length) return null;
    const sorted = [...this.samples].sort((a, b) => a - b);
    const out = {};
    for (const p of ps) {
      const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
      out[`p${p}`] = sorted[idx];
    }
    return out;
  }
}

// ── 日统计：每 (coin/channel) 的计数、缺口、背压丢弃、延迟、覆盖秒数 ──────────

class ChannelDayStats {
  constructor(dayStart, reservoirSize) {
    this.dayStart = dayStart;
    this.count = 0;
    this.lastAt = null;
    this.maxGapMs = 0;
    this.gaps = 0;
    this.inGap = false;
    this.dropped = 0;
    this.latency = new Reservoir(reservoirSize);
    this.seconds = new Uint8Array(86_400);
  }
}

export class DayStats {
  /**
   * @param day UTC 日 YYYY-MM-DD
   * @param options.gapThresholdMs 频道静默超过此值即视为缺口（默认 60s）
   */
  constructor(day, options = {}) {
    this.day = day;
    this.dayStart = dayStartMs(day);
    this.gapThresholdMs = options.gapThresholdMs ?? 60_000;
    this.reservoirSize = options.reservoirSize ?? 20_000;
    this.channels = new Map();
  }

  channel(key) {
    let c = this.channels.get(key);
    if (!c) {
      c = new ChannelDayStats(this.dayStart, this.reservoirSize);
      this.channels.set(key, c);
    }
    return c;
  }

  /**
   * 记录一条消息。r=本机接收毫秒，t=交易所毫秒（可缺）。
   * 返回 {gapEndedMs} 表示该频道刚从缺口中恢复（供日志），否则 null。
   */
  observe(key, r, t) {
    const c = this.channel(key);
    let ended = null;
    if (c.lastAt !== null) {
      const gap = r - c.lastAt;
      if (gap > c.maxGapMs) c.maxGapMs = gap;
      if (c.inGap) {
        ended = { gapEndedMs: gap };
        c.inGap = false;
      }
    }
    c.lastAt = r;
    c.count += 1;
    if (Number.isFinite(t)) c.latency.push(r - t);
    const sec = Math.floor((r - this.dayStart) / 1000);
    if (sec >= 0 && sec < 86_400) c.seconds[sec] = 1;
    return ended;
  }

  dropped(key) {
    this.channel(key).dropped += 1;
  }

  /**
   * 定时扫描：返回本次**新进入**缺口状态的频道 [{key, silentMs}]（每个缺口只报一次）。
   * 从未收到过消息的频道不算缺口（订阅尚未成功另有日志）。
   */
  sweep(nowMs) {
    const started = [];
    for (const [key, c] of this.channels) {
      if (c.lastAt === null || c.inGap) continue;
      const silent = nowMs - c.lastAt;
      if (silent > this.gapThresholdMs) {
        c.inGap = true;
        c.gaps += 1;
        if (silent > c.maxGapMs) c.maxGapMs = silent;
        started.push({ key, silentMs: silent });
      }
    }
    return started;
  }

  /** 汇总为可落盘对象（键为 coin/channel）。 */
  summary(nowMs) {
    const out = {};
    for (const [key, c] of this.channels) {
      let seconds = 0;
      for (let i = 0; i < c.seconds.length; i++) seconds += c.seconds[i];
      // 仍在缺口中的频道：把到 now 的静默计入最大间隔
      let maxGap = c.maxGapMs;
      if (Number.isFinite(nowMs) && c.lastAt !== null) maxGap = Math.max(maxGap, nowMs - c.lastAt);
      out[key] = {
        count: c.count,
        max_gap_ms: Math.round(maxGap),
        gaps: c.gaps,
        dropped: c.dropped,
        seconds_with_data: seconds,
        coverage: Number((seconds / 86_400).toFixed(6)),
        latency_ms: c.latency.percentiles(),
      };
    }
    return out;
  }
}

// ── gzip 完整性 ──────────────────────────────────────────────────────────

/**
 * 流式校验一个 .jsonl.gz：字节数、sha256、解压后行数、gzip 是否完整。
 * 支持多成员拼接（进程重启后追加写入的形态）。尾部截断（强杀）报 Z_BUF_ERROR，
 * 数据损坏报 Z_DATA_ERROR；两种情况 lines 都是「损坏点之前」的行数。
 */
export async function verifyGzipFile(file) {
  const hash = crypto.createHash("sha256");
  let bytes = 0;
  let lines = 0;
  const tee = new Transform({
    transform(chunk, _enc, cb) {
      bytes += chunk.length;
      hash.update(chunk);
      cb(null, chunk);
    },
  });
  const counter = new Writable({
    write(chunk, _enc, cb) {
      for (let i = 0; i < chunk.length; i++) if (chunk[i] === 10) lines += 1;
      cb();
    },
  });
  let gzipOk = true;
  let error = null;
  try {
    await pipeline(fs.createReadStream(file), tee, zlib.createGunzip(), counter);
  } catch (e) {
    gzipOk = false;
    error = e?.code ?? String(e);
  }
  if (!gzipOk) {
    // 解压中途失败时 tee 可能没读完整个文件：sha256/bytes 单独按原始字节再算一遍
    const raw = crypto.createHash("sha256");
    bytes = 0;
    try {
      await pipeline(
        fs.createReadStream(file),
        new Writable({
          write(chunk, _enc, cb) {
            bytes += chunk.length;
            raw.update(chunk);
            cb();
          },
        }),
      );
      return { bytes, lines, sha256: raw.digest("hex"), gzip_ok: false, error };
    } catch (e2) {
      return { bytes, lines, sha256: null, gzip_ok: false, error: e2?.code ?? String(e2) };
    }
  }
  return { bytes, lines, sha256: hash.digest("hex"), gzip_ok: true, error: null };
}

/** 校验一个 <COIN>/<日> 目录下的全部数据文件。 */
export async function verifyDayDir(dir) {
  const files = {};
  for (const name of FILE_NAMES) {
    const file = path.join(dir, `${name}.jsonl.gz`);
    if (!fs.existsSync(file)) continue;
    files[name] = { file: path.basename(file), ...(await verifyGzipFile(file)) };
  }
  return files;
}

/** 生成并原子写入清单。channels 为当日统计（补验时没有，传 null）。 */
export async function writeManifest(dir, { coin, date, channels = null, source = "rotation" }) {
  const manifest = {
    version: MANIFEST_VERSION,
    coin,
    date,
    generated_at: new Date().toISOString(),
    source,
    files: await verifyDayDir(dir),
    channels,
  };
  const target = path.join(dir, MANIFEST_NAME);
  const tmp = `${target}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(manifest, null, 1));
  fs.renameSync(tmp, target);
  return manifest;
}

export function readManifest(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, MANIFEST_NAME), "utf-8"));
  } catch {
    return null;
  }
}

/** 把统计键 coin/channel 归到文件名键（l2book/l2full/trades/bbo/ctx），只取本 coin。 */
export function channelsForCoin(summary, coin) {
  const out = {};
  for (const [key, stats] of Object.entries(summary ?? {})) {
    const [c, ch] = key.split("/");
    if (c !== coin) continue;
    out[CHANNEL_FILE[ch] ?? ch] = stats;
  }
  return Object.keys(out).length ? out : null;
}

/** 列出 <root>/<COIN>/<日> 中早于 today 且缺清单的目录：[{coin, date, dir}] */
export function listUnverifiedDays(root, today) {
  const out = [];
  let coins = [];
  try {
    coins = fs.readdirSync(root, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);
  } catch {
    return out;
  }
  for (const coin of coins) {
    let days = [];
    try {
      days = fs.readdirSync(path.join(root, coin), { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);
    } catch {
      continue;
    }
    for (const date of days.sort()) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date >= today) continue;
      const dir = path.join(root, coin, date);
      if (!fs.existsSync(path.join(dir, MANIFEST_NAME))) out.push({ coin, date, dir });
    }
  }
  return out;
}

// ── 磁盘水位 ─────────────────────────────────────────────────────────────

/** 递归统计目录字节数（文件数量在千量级，直接遍历即可）。 */
export function dirBytes(root) {
  let total = 0;
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    let entries = [];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entries) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) stack.push(p);
      else if (e.isFile()) {
        try {
          total += fs.statSync(p).size;
        } catch {
          /* 文件正在轮转/删除 */
        }
      }
    }
  }
  return total;
}

/** 文件系统可用比例（statfs）。 */
export async function freeRatio(dir) {
  const st = await fs.promises.statfs(dir);
  const total = Number(st.blocks) * Number(st.bsize);
  const free = Number(st.bavail) * Number(st.bsize);
  return { total_bytes: total, free_bytes: free, free_ratio: total > 0 ? free / total : 0 };
}

/**
 * 水位判定（带迟滞）：超过 maxBytes 或可用低于 minFreeRatio → 暂停 bbo；
 * 回落到 maxBytes×0.9 以下且可用高于 minFreeRatio+0.03 才恢复，避免在阈值附近抖动。
 * 返回 {suspend, changed, reason}。
 */
export function diskDecision(currentlySuspended, { bytes, freeRatio: free }, { maxBytes, minFreeRatio }) {
  const over = bytes > maxBytes || free < minFreeRatio;
  const clear = bytes < maxBytes * 0.9 && free > minFreeRatio + 0.03;
  let suspend = currentlySuspended;
  if (!currentlySuspended && over) suspend = true;
  else if (currentlySuspended && clear) suspend = false;
  const reason = over
    ? `data/book ${(bytes / 1e9).toFixed(2)} GB / 上限 ${(maxBytes / 1e9).toFixed(0)} GB，磁盘可用 ${(free * 100).toFixed(1)}% / 下限 ${(minFreeRatio * 100).toFixed(0)}%`
    : "";
  return { suspend, changed: suspend !== currentlySuspended, reason };
}

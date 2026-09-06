#!/usr/bin/env node
/**
 * 盘口录制器：持续订阅 Hyperliquid WebSocket，把订单簿/成交/最优买卖/资产上下文
 * 逐条落盘，为秒级做市研究攒数据。
 *
 * 为什么要录：K 线里没有订单流。做市的 edge 在「谁在挂、谁在撤、队列位置、微观失衡」
 * 里——这些只有盘口有，而交易所不提供历史，只能自己录。
 *
 * 只读公开频道，不需要私钥，主网数据（测试网盘口是假的）。设计成无人值守跑一个月：
 * - 每个 (coin, 频道, UTC 日) 一个 gzip 流，按**本机接收时间**在 UTC 零点轮转，每 10s 同步刷盘。
 *   按接收时间而不是交易所时间切日，是为了零点一到就能确定旧日文件不再被写入，
 *   立刻关流、校验并生成清单（稀疏频道否则会拖住校验）；交易所时间 t 仍逐条记录
 * - 断线指数退避重连并重新订阅；握手 20s 超时；90s 收不到任何消息视为死连接强制重连；
 *   10 分钟仍无消息则优雅关流后以非零码退出，交由 systemd 拉起（兜底任何套接字层的未知状态）
 * - 每分钟一次只读 RTT 探针（info 接口），日切写入清单 rtt_ms，是研究阶段延迟模型的输入
 * - status.json 心跳（每频道最后收到时间与计数、磁盘水位、缺口），watchdog 据此判活
 * - 缺口检测：任一频道 60s 无消息即告警一次（恢复时再记一条）；日切时输出上一日每频道
 *   消息数 / 最大间隔 / 缺口数 / 丢弃数 / 覆盖秒数 / 收包延迟分位数
 * - 完整性：日切后对上一日每个文件流式 gunzip 计行、算 sha256，写入 <COIN>/<日>/manifest.json；
 *   启动时补做所有缺清单的历史日目录
 * - 磁盘水位：data/book 超过 --max-bytes（默认 20 GB）或磁盘可用低于 --min-free-pct（默认 15%）
 *   即告警并暂停录 bbo（最高频、可从 l2book 近似重建），回落后自动恢复；绝不丢 trades / l2Book
 * - 写入背压时丢弃 bbo，同样绝不丢 trades / l2Book
 * - SIGTERM 时刷盘并优雅关闭所有流
 *
 * 用法：
 *   node scripts/record-book.mjs --coins BTC,ETH,SOL,HYPE --out data/book
 *   node scripts/record-book.mjs --testnet            # 测试网（仅联调用）
 *   node scripts/record-book.mjs --l2-slow            # 只订默认 l2Book（20 档 ~0.3Hz），不录 fast 流
 *   node scripts/record-book.mjs --max-bytes 20000000000 --min-free-pct 15 --gap-secs 60
 *
 * 输出（每行一条 JSON）：
 *   <out>/<COIN>/<YYYY-MM-DD>/l2book.jsonl.gz   fast 订阅，5 档/侧 ~2Hz   {t, r, b:[[px,sz,n]…], a:[[px,sz,n]…]}   t=交易所时间 r=本机收到时间
 *   <out>/<COIN>/<YYYY-MM-DD>/l2full.jsonl.gz   默认订阅，20 档/侧 ~0.3Hz，同格式
 *   <out>/<COIN>/<YYYY-MM-DD>/trades.jsonl.gz   {t, r, side, px, sz, tid}
 *   <out>/<COIN>/<YYYY-MM-DD>/bbo.jsonl.gz      {t, r, bid:[px,sz], ask:[px,sz]}
 *   <out>/<COIN>/<YYYY-MM-DD>/ctx.jsonl.gz      {t, r, funding, oi, mark, oracle, mid, premium, vol24h}
 *   <out>/<COIN>/<YYYY-MM-DD>/manifest.json     日切后生成：每文件 bytes/lines/sha256/gzip_ok + 每频道统计
 *   <out>/status.json                            心跳
 */
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import {
  CHANNEL_FILE,
  DayStats,
  Reservoir,
  channelsForCoin,
  dirBytes,
  diskDecision,
  freeRatio,
  listUnverifiedDays,
  utcDay,
  writeManifest,
} from "./book-lib.mjs";

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

const COINS = String(args.coins ?? "BTC,ETH,SOL,HYPE").split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
const OUT = String(args.out ?? "data/book");
const URL = args.testnet === "true" ? "wss://api.hyperliquid-testnet.xyz/ws" : "wss://api.hyperliquid.xyz/ws";
const FLUSH_MS = 10_000;
const PING_MS = 30_000;
const STALE_MS = 90_000;
const STATUS_MS = 10_000;
const GAP_MS = Math.max(5, Number(args["gap-secs"] ?? 60)) * 1000;
const MAX_BYTES = Number(args["max-bytes"] ?? 20e9);
const MIN_FREE_RATIO = Math.max(0, Number(args["min-free-pct"] ?? 15)) / 100;
const CHANNELS = ["l2Book", "trades", "bbo", "activeAssetCtx"];
// l2Book 有两种订阅：默认 = 20 档/侧但被限流到 ~0.3Hz；fast:true = 只有 5 档/侧但 ~2Hz。
// 实测二者可同时订阅，所以两种都录：fast 写 l2book（微观结构主数据），默认写 l2full（深度形状）。
const L2_FAST = args["l2-slow"] !== "true";
const STATUS_KEYS = L2_FAST ? [...CHANNELS, "l2Full"] : CHANNELS;

const log = (m) => process.stdout.write(`${new Date().toISOString()} ${m}\n`);

// ── 文件写入：每 (coin, channel, day) 一个 gzip 流 ─────────────────────────

const streams = new Map(); // key → { gz, file, day, dropped, key, closed: Promise }
const closingByDay = new Map(); // day → Promise[]（日切时等旧日文件真正关闭再校验）

function streamFor(coin, channel, r) {
  const day = utcDay(r);
  const key = `${coin}/${channel}`;
  let s = streams.get(key);
  if (s && s.day !== day) {
    closeStream(key, s);
    s = undefined;
  }
  if (!s) {
    const dir = path.join(OUT, coin, day);
    fs.mkdirSync(dir, { recursive: true });
    const name = CHANNEL_FILE[channel] ?? channel.toLowerCase();
    const filePath = path.join(dir, `${name}.jsonl.gz`);
    // 追加模式：进程重启后同一天的文件接着写（gzip 多成员拼接，zcat/gunzip 都能读）
    const file = fs.createWriteStream(filePath, { flags: "a" });
    const gz = zlib.createGzip({ level: 6 });
    gz.pipe(file);
    gz.on("error", (e) => log(`gzip 流错误 ${key}: ${e}`));
    file.on("error", (e) => log(`文件流错误 ${key}: ${e}`));
    const closed = new Promise((resolve) => {
      file.once("close", resolve);
      file.once("error", resolve);
    });
    s = { gz, file, day, dropped: 0, key, closed };
    streams.set(key, s);
  }
  return s;
}

function closeStream(key, s) {
  try {
    s.gz.end();
  } catch {
    /* 忽略 */
  }
  streams.delete(key);
  if (!closingByDay.has(s.day)) closingByDay.set(s.day, []);
  closingByDay.get(s.day).push(s.closed);
  if (s.dropped) log(`${key} 因背压丢弃 ${s.dropped} 条`);
  return s.closed;
}

function write(coin, channel, r, obj, droppable = false) {
  const key = `${coin}/${channel}`;
  if (droppable && bboSuspended) {
    // 磁盘水位超限：最高频的 bbo 直接不落盘（计入丢弃），其余频道照录
    stats.dropped(key);
    status.suspended_drops += 1;
    return;
  }
  const s = streamFor(coin, channel, r);
  if (droppable && s.gz.writableNeedDrain) {
    // 背压：可丢频道在缓冲未排空前直接丢，绝不阻塞 trades / l2Book
    s.dropped += 1;
    stats.dropped(key);
    return;
  }
  s.gz.write(JSON.stringify(obj) + "\n");
}

function flushAll() {
  for (const s of streams.values()) {
    try {
      s.gz.flush(zlib.constants.Z_SYNC_FLUSH);
    } catch {
      /* 忽略 */
    }
  }
}

// ── 日统计 / 缺口 / 清单 ────────────────────────────────────────────────

let currentDay = utcDay(Date.now());
const ALL_KEYS = [...COINS.flatMap((c) => STATUS_KEYS.map((ch) => `${c}/${ch}`))];
let stats = new DayStats(currentDay, { gapThresholdMs: GAP_MS, keys: ALL_KEYS });
let rotating = false;

function bump(coin, ch, r, t) {
  const key = `${coin}/${ch}`;
  const s = status.channels[key];
  if (s) {
    s.count += 1;
    s.lastAt = r;
  }
  const ended = stats.observe(key, r, Number.isFinite(t) ? t : undefined);
  if (ended) log(`✅ ${key} 缺口结束（静默 ${(ended.gapEndedMs / 1000).toFixed(0)}s）`);
}

function fmtLatency(l) {
  return l ? `p50/p90/p99 ${l.p50}/${l.p90}/${l.p99}ms` : "延迟 n/a";
}

/** UTC 日切：关旧日的流 → 等文件关闭 → 输出上一日统计 → 写每个 coin 的清单。 */
async function rotateDay(now) {
  const today = utcDay(now);
  if (today === currentDay || rotating) return;
  rotating = true;
  const prevDay = currentDay;
  const prevStats = stats;
  currentDay = today;
  stats = new DayStats(today, { gapThresholdMs: GAP_MS, keys: ALL_KEYS });
  const rttPrev = rttDay;
  rttDay = new Reservoir(2_000);
  try {
    for (const [key, s] of [...streams]) if (s.day !== today) closeStream(key, s);
    await Promise.all(closingByDay.get(prevDay) ?? []);
    closingByDay.delete(prevDay);

    const summary = prevStats.summary(now);
    for (const [key, v] of Object.entries(summary)) {
      log(
        `📊 ${prevDay} ${key}: ${v.count} 条 · 最大间隔 ${(v.max_gap_ms / 1000).toFixed(1)}s · 缺口 ${v.gaps} · 丢弃 ${v.dropped}` +
          ` · 覆盖 ${(v.coverage * 100).toFixed(1)}% · ${fmtLatency(v.latency_ms)}`,
      );
    }
    for (const coin of COINS) {
      const dir = path.join(OUT, coin, prevDay);
      if (!fs.existsSync(dir)) continue;
      try {
        const m = await writeManifest(dir, {
          coin, date: prevDay, channels: channelsForCoin(summary, coin), source: "rotation",
          extra: { rtt_ms: rttPrev.count ? { ...rttPrev.percentiles(), n: rttPrev.count } : null },
        });
        const bad = Object.entries(m.files).filter(([, f]) => !f.gzip_ok).map(([n, f]) => `${n}:${f.error}`);
        log(`🧾 ${coin}/${prevDay} 清单已写入（${Object.keys(m.files).length} 个文件${bad.length ? `，⚠️ gzip 异常 ${bad.join(" ")}` : ""}）`);
      } catch (e) {
        log(`❌ ${coin}/${prevDay} 清单生成失败: ${e}`);
      }
    }
    status.last_manifest_day = prevDay;
  } finally {
    rotating = false;
  }
}

/** 启动补验：历史日目录缺清单的（崩溃/重启造成），串行补做，不与连接抢资源。 */
async function catchUpManifests() {
  const missing = listUnverifiedDays(OUT, currentDay);
  if (!missing.length) return;
  log(`🧾 发现 ${missing.length} 个历史日目录缺清单，开始补验`);
  for (const { coin, date, dir } of missing) {
    try {
      const m = await writeManifest(dir, { coin, date, channels: null, source: "catch-up" });
      const bad = Object.entries(m.files).filter(([, f]) => !f.gzip_ok).map(([n, f]) => `${n}:${f.error}`);
      log(`🧾 ${coin}/${date} 补验完成（${Object.keys(m.files).length} 个文件${bad.length ? `，⚠️ gzip 异常 ${bad.join(" ")}` : ""}）`);
    } catch (e) {
      log(`❌ ${coin}/${date} 补验失败: ${e}`);
    }
  }
}

// ── 磁盘水位 ──────────────────────────────────────────────────────────

let bboSuspended = false;
let lastDiskWarnAt = 0;

async function checkDisk() {
  let bytes;
  let fr;
  try {
    bytes = dirBytes(OUT);
    fr = await freeRatio(OUT);
  } catch (e) {
    log(`磁盘水位检查失败: ${e}`);
    return;
  }
  const decision = diskDecision(bboSuspended, { bytes, freeRatio: fr.free_ratio }, { maxBytes: MAX_BYTES, minFreeRatio: MIN_FREE_RATIO });
  status.disk = { bytes, free_pct: Number((fr.free_ratio * 100).toFixed(2)), bbo_suspended: decision.suspend };
  const now = Date.now();
  if (decision.changed) {
    bboSuspended = decision.suspend;
    lastDiskWarnAt = now;
    log(decision.suspend ? `🛑 磁盘水位告警：${decision.reason}，暂停录制 bbo（其余频道照录）` : "✅ 磁盘水位回落，恢复录制 bbo");
  } else if (bboSuspended && now - lastDiskWarnAt > 3_600_000) {
    lastDiskWarnAt = now;
    log(`🛑 磁盘水位持续超限：${decision.reason}，bbo 仍暂停`);
  }
}

// ── 心跳 ──────────────────────────────────────────────────────────────

const status = {
  startedAt: new Date().toISOString(),
  url: URL,
  coins: COINS,
  connected: false,
  reconnects: 0,
  lastMessageAt: null,
  day: currentDay,
  disk: null,
  suspended_drops: 0,
  last_manifest_day: null,
  rtt_ms_last: null,
  rtt_probe_error: null,
  channels: {},
};
for (const c of COINS) for (const ch of STATUS_KEYS) status.channels[`${c}/${ch}`] = { count: 0, lastAt: null };

function writeStatus() {
  try {
    fs.mkdirSync(OUT, { recursive: true });
    const tmp = path.join(OUT, ".status.tmp");
    const gapsOpen = [...stats.channels.entries()].filter(([, c]) => c.inGap).map(([k]) => k);
    // 判活请看 last_message_age_s / stale，不要看 updatedAt：心跳在连接死掉时照样新鲜
    const ageS = Math.round((Date.now() - (status.lastMessageAt ?? startedAtMs)) / 1000);
    fs.writeFileSync(
      tmp,
      JSON.stringify({ ...status, day: currentDay, gaps_open: gapsOpen, last_message_age_s: ageS, stale: ageS * 1000 > STALE_MS, updatedAt: new Date().toISOString() }, null, 1),
    );
    fs.renameSync(tmp, path.join(OUT, "status.json"));
  } catch (e) {
    log(`status.json 写入失败: ${e}`);
  }
}

// ── WebSocket ─────────────────────────────────────────────────────────
//
// 重连的三条硬规则（2026-09-05 的事故：重连握手失败后 close 事件没来，看门狗对一个
// 已死的套接字反复 close()，永远走不到 connect()，静默丢了 15 小时数据）：
//   ① 每个套接字的 error / close 只取先到者安排重连（settled 标记），握手超时同样算；
//   ② 看门狗发现无消息时不依赖旧套接字的事件——丢弃引用、直接安排重连；
//   ③ 兜底：无论如何 10 分钟收不到任何消息就优雅关流后以非零码退出，交由 systemd 拉起。

const INFO_URL = args.testnet === "true" ? "https://api.hyperliquid-testnet.xyz/info" : "https://api.hyperliquid.xyz/info";
const HANDSHAKE_TIMEOUT_MS = 20_000;
const SUBSCRIBE_GRACE_MS = 2_000;
const SELF_EXIT_STALE_MS = 10 * 60_000;
const RTT_PROBE_MS = 60_000;
const startedAtMs = Date.now();

let ws = null;
let backoff = 1000;
let pingTimer = null;
let reconnectTimer = null;
let stopping = false;
/** 本连接 open 时刻：订阅后头 2 秒交易所会推历史快照（trades 等），其收包延迟不真实，不计入分位数 */
let connectedAt = 0;

function scheduleReconnect(reason) {
  if (stopping || reconnectTimer) return;
  status.connected = false;
  status.reconnects += 1;
  log(`${reason}，${backoff / 1000}s 后重连`);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, backoff);
  backoff = Math.min(backoff * 2, 30_000);
}

function subscribe(sock) {
  for (const coin of COINS) {
    for (const type of CHANNELS) {
      sock.send(JSON.stringify({ method: "subscribe", subscription: { type, coin } }));
      if (type === "l2Book" && L2_FAST) sock.send(JSON.stringify({ method: "subscribe", subscription: { type, coin, fast: true } }));
    }
  }
}

function handle(msg) {
  const r = Date.now();
  status.lastMessageAt = r;
  const ch = msg.channel;
  if (ch === "pong" || ch === "subscriptionResponse") return;
  if (ch === "error") {
    log(`交易所报错: ${JSON.stringify(msg.data).slice(0, 200)}`);
    return;
  }
  const d = msg.data;
  if (!d) return;
  // 订阅初期的快照不计延迟
  const lat = (t) => (r - connectedAt > SUBSCRIBE_GRACE_MS ? t : undefined);

  if (ch === "l2Book") {
    const coin = d.coin;
    const lv = (side) => (d.levels?.[side] ?? []).map((x) => [x.px, x.sz, x.n]);
    const b = lv(0);
    const a = lv(1);
    // fast 订阅只回 5 档，默认订阅回 20 档，按档数分流（四个主流标的的完整簿永远 >5 档）
    const key = L2_FAST && (b.length > 5 || a.length > 5) ? "l2Full" : "l2Book";
    bump(coin, key, r, lat(d.time));
    write(coin, key, r, { t: d.time, r, b, a });
  } else if (ch === "trades") {
    for (const tr of d) {
      bump(tr.coin, ch, r, lat(tr.time));
      // 不存 hash 与 users：研究用不上，且没必要落地址
      write(tr.coin, ch, r, { t: tr.time, r, side: tr.side, px: tr.px, sz: tr.sz, tid: tr.tid });
    }
  } else if (ch === "bbo") {
    const coin = d.coin;
    bump(coin, ch, r, lat(d.time));
    const bid = d.bbo?.[0] ? [d.bbo[0].px, d.bbo[0].sz] : null;
    const ask = d.bbo?.[1] ? [d.bbo[1].px, d.bbo[1].sz] : null;
    write(coin, ch, r, { t: d.time, r, bid, ask }, true);
  } else if (ch === "activeAssetCtx") {
    const coin = d.coin;
    const c = d.ctx ?? {};
    bump(coin, ch, r, undefined); // ctx 没有交易所时间戳，不计延迟
    write(coin, ch, r, {
      t: r, r,
      funding: c.funding, oi: c.openInterest, mark: c.markPx, oracle: c.oraclePx,
      mid: c.midPx, premium: c.premium, vol24h: c.dayNtlVlm,
    });
  }
}

function connect() {
  if (stopping) return;
  log(`连接 ${URL}（${COINS.join(" ")}）`);
  let sock;
  try {
    sock = new WebSocket(URL);
  } catch (e) {
    scheduleReconnect(`创建连接失败: ${e}`);
    return;
  }
  ws = sock;
  let settled = false;
  const settle = (reason) => {
    if (settled) return;
    settled = true;
    clearTimeout(handshake);
    if (ws === sock) ws = null;
    clearInterval(pingTimer);
    scheduleReconnect(reason);
  };
  const handshake = setTimeout(() => {
    if (sock.readyState === WebSocket.CONNECTING) {
      try {
        sock.close();
      } catch {
        /* 忽略 */
      }
      settle(`握手 ${HANDSHAKE_TIMEOUT_MS / 1000}s 未完成`);
    }
  }, HANDSHAKE_TIMEOUT_MS);
  sock.onopen = () => {
    clearTimeout(handshake);
    status.connected = true;
    backoff = 1000;
    connectedAt = Date.now();
    subscribe(sock);
    log("已订阅 " + COINS.length * STATUS_KEYS.length + " 个频道");
    clearInterval(pingTimer);
    pingTimer = setInterval(() => {
      try {
        sock.send(JSON.stringify({ method: "ping" }));
      } catch {
        /* 忽略 */
      }
    }, PING_MS);
  };
  sock.onmessage = (e) => {
    try {
      handle(JSON.parse(e.data));
    } catch (err) {
      log(`消息处理异常: ${err}`);
    }
  };
  sock.onerror = (e) => {
    log(`socket 错误: ${e?.message ?? e}`);
    // 握手阶段或已关闭的套接字出错时未必再来 close 事件：这里就安排重连
    if (sock.readyState !== WebSocket.OPEN) settle("连接失败");
  };
  sock.onclose = (e) => {
    if (stopping) return;
    settle(`连接关闭（code ${e?.code}）`);
  };
}

/** 往返时延探针：只读 info 接口，量生产机到交易所的 RTT（研究阶段延迟模型的输入）。 */
let rttDay = new Reservoir(2_000);
async function probeRtt() {
  if (stopping) return;
  const t0 = performance.now();
  try {
    const resp = await fetch(INFO_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "allMids" }),
      signal: AbortSignal.timeout(10_000),
    });
    await resp.arrayBuffer();
    if (resp.ok) {
      const rtt = Math.round(performance.now() - t0);
      rttDay.push(rtt);
      status.rtt_ms_last = rtt;
      status.rtt_probe_error = null;
    } else {
      status.rtt_probe_error = `HTTP ${resp.status}`;
    }
  } catch (e) {
    status.rtt_probe_error = String(e?.message ?? e).slice(0, 80);
  }
}

// 看门狗（15s）：频道缺口扫描 → 死连接处理 → 10 分钟无消息自杀兜底
setInterval(() => {
  if (stopping) return;
  const now = Date.now();
  for (const { key, silentMs } of stats.sweep(now)) {
    log(`⚠️ ${key} ${(silentMs / 1000).toFixed(0)}s 无消息（缺口开始）${key.endsWith("/trades") ? "——成交稀疏可能属正常" : ""}`);
  }
  const silent = now - (status.lastMessageAt ?? startedAtMs);
  if (silent > SELF_EXIT_STALE_MS) {
    log(`🛑 ${Math.round(silent / 60000)} 分钟没有收到任何消息且重连无效，退出交由 systemd 重新拉起`);
    shutdown("stale", 2);
    return;
  }
  if (silent <= STALE_MS) return;
  if (ws && ws.readyState === WebSocket.OPEN) {
    // 连接看似正常却没有数据：不等它的事件，直接丢弃并重连
    log(`${STALE_MS / 1000}s 无消息，判定死连接，强制重连`);
    const old = ws;
    ws = null;
    clearInterval(pingTimer);
    scheduleReconnect("死连接");
    try {
      old.close();
    } catch {
      /* 忽略 */
    }
  } else if (!ws && !reconnectTimer) {
    log("无连接且无重连计划，立即重连");
    connect();
  }
  // CONNECTING 由握手超时处理；CLOSING 等它的 close 事件（settle 会安排重连）
}, 15_000);

setInterval(flushAll, FLUSH_MS);
setInterval(writeStatus, STATUS_MS);
setInterval(() => void rotateDay(Date.now()).catch((e) => log(`日切异常: ${e}`)), 5_000);
setInterval(() => void checkDisk(), 60_000);
setInterval(() => void probeRtt(), RTT_PROBE_MS);

function shutdown(sig, code = 0) {
  if (stopping) return;
  stopping = true;
  log(`收到 ${sig}，刷盘并关闭`);
  clearInterval(pingTimer);
  clearTimeout(reconnectTimer);
  try {
    ws?.close();
  } catch {
    /* 忽略 */
  }
  const pending = [];
  for (const [key, s] of [...streams]) {
    // 等文件描述符真正关闭（gzip 尾部落盘），不是 gzip 流 finish
    pending.push(closeStream(key, s));
  }
  writeStatus();
  const t0 = Date.now();
  Promise.all(pending).then(() => {
    log(`已关闭 ${pending.length} 个流（${Date.now() - t0}ms）`);
    process.exit(code);
  });
  setTimeout(() => {
    log("关流超时，强制退出（最后一段 gzip 可能缺尾部，数据本身已按 10s 同步刷盘）");
    process.exit(code || 0);
  }, 12_000);
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

fs.mkdirSync(OUT, { recursive: true });
writeStatus();
connect();
void checkDisk();
void probeRtt();
// 补验放在连接之后、低优先级：不与订阅握手抢 CPU
setTimeout(() => void catchUpManifests().catch((e) => log(`补验异常: ${e}`)), 5_000);

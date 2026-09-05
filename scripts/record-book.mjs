#!/usr/bin/env node
/**
 * 盘口录制器：持续订阅 Hyperliquid WebSocket，把订单簿/成交/最优买卖/资产上下文
 * 逐条落盘，为秒级做市研究攒数据。
 *
 * 为什么要录：K 线里没有订单流。做市的 edge 在「谁在挂、谁在撤、队列位置、微观失衡」
 * 里——这些只有盘口有，而交易所不提供历史，只能自己录。
 *
 * 只读公开频道，不需要私钥，主网数据（测试网盘口是假的）。设计成无人值守跑一个月：
 * - 每个 (coin, 频道, UTC 日) 一个 gzip 流，UTC 零点轮转，每 10s 同步刷盘
 * - 断线指数退避重连并重新订阅；90s 收不到任何消息视为死连接强制重连
 * - status.json 心跳（每频道最后收到时间与计数），watchdog 据此判活
 * - 写入背压时丢弃 bbo（最高频、可从 l2Book 近似重建），绝不丢 trades / l2Book
 * - SIGTERM 时刷盘并优雅关闭所有流
 *
 * 用法：
 *   node scripts/record-book.mjs --coins BTC,ETH,SOL,HYPE --out data/book
 *   node scripts/record-book.mjs --testnet            # 测试网（仅联调用）
 *   node scripts/record-book.mjs --l2-slow            # 只订默认 l2Book（20 档 ~0.3Hz），不录 fast 流
 *
 * 磁盘：BTC 约 50MB/天（l2book 11 + bbo 14 + trades 20 + 其余），四个标的一个月约 6GB。
 *
 * 输出（每行一条 JSON）：
 *   <out>/<COIN>/<YYYY-MM-DD>/l2book.jsonl.gz   fast 订阅，5 档/侧 ~2Hz   {t, r, b:[[px,sz,n]…], a:[[px,sz,n]…]}   t=交易所时间 r=本机收到时间
 *   <out>/<COIN>/<YYYY-MM-DD>/l2full.jsonl.gz   默认订阅，20 档/侧 ~0.3Hz，同格式
 *   <out>/<COIN>/<YYYY-MM-DD>/trades.jsonl.gz   {t, r, side, px, sz, tid}
 *   <out>/<COIN>/<YYYY-MM-DD>/bbo.jsonl.gz      {t, r, bid:[px,sz], ask:[px,sz]}
 *   <out>/<COIN>/<YYYY-MM-DD>/ctx.jsonl.gz      {t, r, funding, oi, mark, oracle, mid, premium, vol24h}
 *   <out>/status.json                            心跳
 */
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

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
const CHANNELS = ["l2Book", "trades", "bbo", "activeAssetCtx"];
// l2Book 有两种订阅：默认 = 20 档/侧但被限流到 ~0.3Hz；fast:true = 只有 5 档/侧但 ~2Hz。
// 实测二者可同时订阅，所以两种都录：fast 写 l2book（微观结构主数据），默认写 l2full（深度形状）。
const L2_FAST = args["l2-slow"] !== "true";
const STATUS_KEYS = L2_FAST ? [...CHANNELS, "l2Full"] : CHANNELS;

const log = (m) => process.stdout.write(`${new Date().toISOString()} ${m}\n`);

// ── 文件写入：每 (coin, channel, day) 一个 gzip 流 ─────────────────────────

const streams = new Map(); // key → { gz, file, day, dropped }
const utcDay = (t) => new Date(t).toISOString().slice(0, 10);

function streamFor(coin, channel, t) {
  const day = utcDay(t);
  const key = `${coin}/${channel}`;
  let s = streams.get(key);
  if (s && s.day !== day) {
    closeStream(key, s);
    s = undefined;
  }
  if (!s) {
    const dir = path.join(OUT, coin, day);
    fs.mkdirSync(dir, { recursive: true });
    const name = channel === "activeAssetCtx" ? "ctx" : channel.toLowerCase();
    const filePath = path.join(dir, `${name}.jsonl.gz`);
    // 追加模式：进程重启后同一天的文件接着写（gzip 多成员拼接，zcat/gunzip 都能读）
    const file = fs.createWriteStream(filePath, { flags: "a" });
    const gz = zlib.createGzip({ level: 6 });
    gz.pipe(file);
    gz.on("error", (e) => log(`gzip 流错误 ${key}: ${e}`));
    file.on("error", (e) => log(`文件流错误 ${key}: ${e}`));
    s = { gz, file, day, dropped: 0, key };
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
  if (s.dropped) log(`${key} 因背压丢弃 ${s.dropped} 条`);
}

function write(coin, channel, t, obj, droppable = false) {
  const s = streamFor(coin, channel, t);
  const ok = s.gz.write(JSON.stringify(obj) + "\n");
  if (!ok && droppable) {
    // 背压：后续可丢频道直接丢，直到 drain
    s.gz.once("drain", () => {});
    s.dropped += 1;
  }
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

// ── 心跳 ──────────────────────────────────────────────────────────────

const status = {
  startedAt: new Date().toISOString(),
  url: URL,
  coins: COINS,
  connected: false,
  reconnects: 0,
  lastMessageAt: null,
  channels: {},
};
for (const c of COINS) for (const ch of STATUS_KEYS) status.channels[`${c}/${ch}`] = { count: 0, lastAt: null };

function writeStatus() {
  try {
    fs.mkdirSync(OUT, { recursive: true });
    const tmp = path.join(OUT, ".status.tmp");
    fs.writeFileSync(tmp, JSON.stringify({ ...status, updatedAt: new Date().toISOString() }, null, 1));
    fs.renameSync(tmp, path.join(OUT, "status.json"));
  } catch (e) {
    log(`status.json 写入失败: ${e}`);
  }
}

// ── WebSocket ─────────────────────────────────────────────────────────

let ws = null;
let backoff = 1000;
let pingTimer = null;
let stopping = false;

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

  if (ch === "l2Book") {
    const coin = d.coin;
    const lv = (side) => (d.levels?.[side] ?? []).map((x) => [x.px, x.sz, x.n]);
    const b = lv(0);
    const a = lv(1);
    // fast 订阅只回 5 档，默认订阅回 20 档，按档数分流（四个主流标的的完整簿永远 >5 档）
    const key = L2_FAST && (b.length > 5 || a.length > 5) ? "l2Full" : "l2Book";
    bump(coin, key, r);
    write(coin, key, d.time ?? r, { t: d.time, r, b, a });
  } else if (ch === "trades") {
    for (const tr of d) {
      bump(tr.coin, ch, r);
      // 不存 hash 与 users：研究用不上，且没必要落地址
      write(tr.coin, ch, tr.time ?? r, { t: tr.time, r, side: tr.side, px: tr.px, sz: tr.sz, tid: tr.tid });
    }
  } else if (ch === "bbo") {
    const coin = d.coin;
    bump(coin, ch, r);
    const bid = d.bbo?.[0] ? [d.bbo[0].px, d.bbo[0].sz] : null;
    const ask = d.bbo?.[1] ? [d.bbo[1].px, d.bbo[1].sz] : null;
    write(coin, ch, d.time ?? r, { t: d.time, r, bid, ask }, true);
  } else if (ch === "activeAssetCtx") {
    const coin = d.coin;
    const c = d.ctx ?? {};
    bump(coin, ch, r);
    write(coin, ch, r, {
      t: r, r,
      funding: c.funding, oi: c.openInterest, mark: c.markPx, oracle: c.oraclePx,
      mid: c.midPx, premium: c.premium, vol24h: c.dayNtlVlm,
    });
  }
}

function bump(coin, ch, r) {
  const s = status.channels[`${coin}/${ch}`];
  if (s) {
    s.count += 1;
    s.lastAt = r;
  }
}

function connect() {
  if (stopping) return;
  log(`连接 ${URL}（${COINS.join(" ")}）`);
  const sock = new WebSocket(URL);
  ws = sock;
  sock.onopen = () => {
    status.connected = true;
    backoff = 1000;
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
  sock.onerror = (e) => log(`socket 错误: ${e?.message ?? e}`);
  sock.onclose = (e) => {
    status.connected = false;
    clearInterval(pingTimer);
    if (stopping) return;
    status.reconnects += 1;
    log(`连接关闭（code ${e?.code}），${backoff / 1000}s 后重连`);
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 30_000);
  };
}

// 死连接检测：90s 没有任何消息就主动断开触发重连
setInterval(() => {
  if (stopping || !ws) return;
  if (status.lastMessageAt && Date.now() - status.lastMessageAt > STALE_MS) {
    log(`${STALE_MS / 1000}s 无消息，判定死连接，强制重连`);
    try {
      ws.close();
    } catch {
      connect();
    }
  }
}, 15_000);

setInterval(flushAll, FLUSH_MS);
setInterval(writeStatus, STATUS_MS);

function shutdown(sig) {
  if (stopping) return;
  stopping = true;
  log(`收到 ${sig}，刷盘并关闭`);
  clearInterval(pingTimer);
  try {
    ws?.close();
  } catch {
    /* 忽略 */
  }
  const pending = [];
  for (const [key, s] of streams) {
    pending.push(new Promise((resolve) => {
      // 等文件描述符真正关闭（gzip 尾部落盘），不是 gzip 流 finish
      s.file.once("close", resolve);
      s.file.once("error", resolve);
      s.gz.end();
    }));
    streams.delete(key);
  }
  writeStatus();
  const t0 = Date.now();
  Promise.all(pending).then(() => {
    log(`已关闭 ${pending.length} 个流（${Date.now() - t0}ms）`);
    process.exit(0);
  });
  setTimeout(() => {
    log("关流超时，强制退出（最后一段 gzip 可能缺尾部，数据本身已按 10s 同步刷盘）");
    process.exit(0);
  }, 12_000);
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

fs.mkdirSync(OUT, { recursive: true });
writeStatus();
connect();

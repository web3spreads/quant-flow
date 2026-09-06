#!/usr/bin/env node
/**
 * 盘口录制器健康提醒：把「数据还在不在录」推到人看得见的地方。
 *
 * 教训：2026-09-05 断流 15 小时，心跳文件却一直新鲜，没有任何通道告诉人。
 * 判活口径只看 status.json 的 last_message_age_s / stale，不看 updatedAt。
 *
 * 用法：
 *   node scripts/book-health.mjs --daily [--book data/book]      # 日报：昨日各标的覆盖率/缺口/gzip/备份 + 当前状态
 *   node scripts/book-health.mjs --check [--book data/book]      # 巡检：异常时告警一次、恢复时再报一次（状态文件去重）
 *   node scripts/book-health.mjs --daily --print                 # 只打印不发送
 *
 * 通知目标：环境变量 QUANTFLOW_NOTIFY_URLS（逗号分隔，Apprise 风格）：
 *   larksuite://<token>  lark://<token>  feishu://<token>  tgram://<botToken>/<chatId>  json(s)://host/path
 * 未设置时只打印。
 */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

export const STALE_S = 90;
export const HEARTBEAT_S = 60;
export const STATE_FILE = ".health-state.json";
const COINS_DEFAULT = ["BTC", "ETH", "SOL", "HYPE"];

export const utcDay = (ms) => new Date(ms).toISOString().slice(0, 10);

// ── 通知通道（与 PolySnipe 的 URL 语法一致的子集） ──────────────────────

export function parseChannel(url) {
  const sep = url.indexOf("://");
  if (sep < 0) throw new Error(`不支持的通知 URL: ${url.slice(0, 12)}…`);
  const scheme = url.slice(0, sep).toLowerCase();
  const rest = url.slice(sep + 3);
  if (scheme === "lark" || scheme === "feishu" || scheme === "larksuite") {
    const token = rest.replace(/^\/+|\/+$/g, "");
    if (!/^[A-Za-z0-9-]+$/.test(token)) throw new Error(`通知 URL 令牌非法: ${scheme}://…`);
    const host = scheme === "larksuite" ? "open.larksuite.com" : "open.feishu.cn";
    return (title, body) => ({
      url: `https://${host}/open-apis/bot/v2/hook/${token}`,
      init: jsonPost({ msg_type: "text", content: { text: `${title}\n${body}` } }),
    });
  }
  if (scheme === "tgram" || scheme === "telegram") {
    const [botToken, chatId] = rest.split("/").filter(Boolean);
    if (!botToken || !chatId) throw new Error("tgram:// 需要 botToken/chatId");
    return (title, body) => ({
      url: `https://api.telegram.org/bot${botToken}/sendMessage`,
      init: jsonPost({ chat_id: chatId, text: `${title}\n${body}` }),
    });
  }
  if (scheme === "json" || scheme === "jsons") {
    return (title, body, type) => ({
      url: `${scheme === "jsons" ? "https" : "http"}://${rest}`,
      init: jsonPost({ title, body, type }),
    });
  }
  throw new Error(`不支持的通知 URL scheme: ${scheme}://…`);
}

function jsonPost(payload) {
  return { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) };
}

export async function notify(urls, title, body, type = "info") {
  const results = [];
  for (const raw of urls) {
    const url = raw.trim();
    if (!url) continue;
    let build;
    try {
      build = parseChannel(url);
    } catch (e) {
      results.push({ ok: false, error: String(e.message) });
      continue;
    }
    const req = build(title, body, type);
    try {
      const resp = await fetch(req.url, { ...req.init, signal: AbortSignal.timeout(15_000) });
      results.push({ ok: resp.ok, status: resp.status });
    } catch (e) {
      results.push({ ok: false, error: String(e?.message ?? e).slice(0, 80) });
    }
  }
  return results;
}

// ── 读取状态 ──────────────────────────────────────────────────────────

export function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch {
    return null;
  }
}

/** 从 status.json 与文件系统整理出当前状态（纯函数，便于测试）。 */
export function assess(status, nowMs, opts = {}) {
  const heartbeatAge = status?.updatedAt ? (nowMs - Date.parse(status.updatedAt)) / 1000 : Infinity;
  const msgAge = status?.last_message_age_s ?? (status?.lastMessageAt ? (nowMs - status.lastMessageAt) / 1000 : Infinity);
  const problems = [];
  if (!status) problems.push("status.json 不存在或不可读");
  else if (heartbeatAge > (opts.heartbeatS ?? HEARTBEAT_S)) problems.push(`心跳停止 ${Math.round(heartbeatAge)}s（进程可能已死）`);
  else if (status.stale || msgAge > (opts.staleS ?? STALE_S)) problems.push(`断流 ${Math.round(msgAge)}s 无消息`);
  if (status?.disk?.bbo_suspended) problems.push(`磁盘水位超限，bbo 已暂停（${(status.disk.bytes / 1e9).toFixed(1)} GB，可用 ${status.disk.free_pct}%）`);
  if (status?.gaps_open?.length) problems.push(`缺口中: ${status.gaps_open.join(" ")}`);
  return { problems, heartbeatAge, msgAge };
}

/** 昨日清单汇总（纯函数）。 */
export function summarizeDay(book, day, coins) {
  const rows = [];
  for (const coin of coins) {
    const dir = path.join(book, coin, day);
    const m = readJson(path.join(dir, "manifest.json"));
    if (!m) {
      rows.push({ coin, missing: true });
      continue;
    }
    const l2 = m.channels?.l2book ?? {};
    const bad = Object.entries(m.files ?? {}).filter(([, f]) => !f.gzip_ok).map(([n]) => n);
    rows.push({
      coin,
      coverage: l2.coverage ?? null,
      gaps: l2.gaps ?? null,
      maxGapS: l2.max_gap_ms != null ? Math.round(l2.max_gap_ms / 1000) : null,
      latencyP50: l2.latency_ms?.p50 ?? null,
      rttP50: m.rtt_ms?.p50 ?? null,
      bad,
      backedUp: fs.existsSync(path.join(dir, ".backed-up")),
      source: m.source,
    });
  }
  return rows;
}

/** 到目前为止达到覆盖率门槛的标的日数（研究样本进度）。 */
export function qualifiedDays(book, coins, minCoverage = 0.95) {
  const out = {};
  for (const coin of coins) {
    let n = 0;
    let days = [];
    try {
      days = fs.readdirSync(path.join(book, coin)).filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d));
    } catch {
      /* 无目录 */
    }
    for (const d of days) {
      const m = readJson(path.join(book, coin, d, "manifest.json"));
      const cov = m?.channels?.l2book?.coverage;
      if (cov != null && cov >= minCoverage && Object.values(m.files ?? {}).every((f) => f.gzip_ok)) n += 1;
    }
    out[coin] = n;
  }
  return out;
}

export function formatDaily(day, rows, status, assessed, qualified) {
  const pct = (v) => (v == null ? "-" : `${(v * 100).toFixed(1)}%`);
  const lines = rows.map((r) =>
    r.missing
      ? `${r.coin}: 无清单`
      : `${r.coin}: 覆盖 ${pct(r.coverage)} 缺口 ${r.gaps ?? "-"} 最大 ${r.maxGapS ?? "-"}s 延迟 ${r.latencyP50 ?? "-"}ms RTT ${r.rttP50 ?? "-"}ms` +
        `${r.bad.length ? ` ⚠️gzip ${r.bad.join(",")}` : ""}${r.backedUp ? " 备份✓" : " 备份✗"}`,
  );
  const disk = status?.disk ? `磁盘 ${(status.disk.bytes / 1e9).toFixed(2)} GB / 可用 ${status.disk.free_pct}%` : "磁盘 -";
  const now = assessed.problems.length ? `⚠️ ${assessed.problems.join("；")}` : `✅ 在录（最近消息 ${Math.round(assessed.msgAge)}s 前，重连 ${status?.reconnects ?? "-"} 次）`;
  const q = Object.entries(qualified).map(([c, n]) => `${c} ${n}`).join(" ");
  return [`昨日 ${day}`, ...lines, `现在：${now}，${disk}`, `合格日累计：${q}`].join("\n");
}

// ── 主流程 ────────────────────────────────────────────────────────────

function parseArgs(argv) {
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
  return args;
}

export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const book = String(args.book ?? "data/book");
  const coins = String(args.coins ?? COINS_DEFAULT.join(",")).split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
  const urls = args.print === "true" ? [] : String(process.env.QUANTFLOW_NOTIFY_URLS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const now = Date.now();
  const status = readJson(path.join(book, "status.json"));
  const assessed = assess(status, now);
  const title = "Quant Flow 盘口录制器";

  if (args.daily === "true") {
    const day = utcDay(now - 86_400_000);
    const body = formatDaily(day, summarizeDay(book, day, coins), status, assessed, qualifiedDays(book, coins));
    console.log(body);
    if (urls.length) console.log(JSON.stringify(await notify(urls, `${title} 日报`, body, assessed.problems.length ? "warning" : "info")));
    return 0;
  }

  if (args.check === "true") {
    const stateFile = path.join(book, STATE_FILE);
    const prev = readJson(stateFile) ?? { alerting: [] };
    // 01:00 UTC 之后昨日清单仍缺 → 也算异常（日切失败）
    const day = utcDay(now - 86_400_000);
    if (new Date(now).getUTCHours() >= 1) {
      const missing = coins.filter((c) => !fs.existsSync(path.join(book, c, day, "manifest.json")));
      if (missing.length) assessed.problems.push(`昨日 ${day} 清单缺失: ${missing.join(" ")}`);
    }
    const current = assessed.problems;
    const newOnes = current.filter((p) => !prev.alerting.includes(p));
    const cleared = prev.alerting.filter((p) => !current.includes(p));
    let sent = null;
    if (newOnes.length) {
      const body = `🛑 ${newOnes.join("\n")}\n（判活口径：last_message_age_s / stale）`;
      console.log(body);
      if (urls.length) sent = await notify(urls, `${title} 告警`, body, "failure");
    } else if (cleared.length && !current.length) {
      const body = `✅ 已恢复：${cleared.join("；")}\n最近消息 ${Math.round(assessed.msgAge)}s 前`;
      console.log(body);
      if (urls.length) sent = await notify(urls, `${title} 恢复`, body, "success");
    } else {
      console.log(current.length ? `仍在告警中: ${current.join("；")}` : `正常（最近消息 ${Math.round(assessed.msgAge)}s 前）`);
    }
    try {
      fs.writeFileSync(stateFile, JSON.stringify({ alerting: current, at: new Date(now).toISOString() }));
    } catch (e) {
      console.error(`状态文件写入失败: ${e}`);
    }
    if (sent) console.log(JSON.stringify(sent));
    return current.length ? 1 : 0;
  }

  console.error("用法: --daily | --check [--book DIR] [--coins BTC,ETH] [--print]");
  return 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().then((code) => process.exit(code), (e) => {
    console.error(e);
    process.exit(2);
  });
}

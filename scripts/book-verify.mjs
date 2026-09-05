#!/usr/bin/env node
/**
 * 盘口数据校验：对 <dir>/<COIN>/<日> 逐目录流式校验 gzip 完整性、行数与 sha256，
 * 与清单（manifest.json）比对。备份端复核、研究前质量检查都用它。
 *
 * 用法：
 *   node scripts/book-verify.mjs --dir data/book                 # 全部历史日（不含今天）
 *   node scripts/book-verify.mjs --dir data/book --coin BTC --date 2026-09-04
 *   node scripts/book-verify.mjs --dir data/book --write         # 缺清单的目录补写清单（无频道统计）
 *   node scripts/book-verify.mjs --dir data/book --json          # 机器可读输出
 *
 * 退出码：0 全部一致；1 有损坏文件或与清单不一致；2 参数/目录错误。
 */
import fs from "node:fs";
import path from "node:path";
import { MANIFEST_NAME, readManifest, utcDay, verifyDayDir, writeManifest } from "./book-lib.mjs";

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

const ROOT = String(args.dir ?? "data/book");
const ONLY_COIN = args.coin ? String(args.coin).toUpperCase() : null;
const ONLY_DATE = args.date ? String(args.date) : null;
const WRITE = args.write === "true";
const JSON_OUT = args.json === "true";
const today = utcDay(Date.now());

if (!fs.existsSync(ROOT)) {
  process.stderr.write(`目录不存在: ${ROOT}\n`);
  process.exit(2);
}

const targets = [];
for (const coin of fs.readdirSync(ROOT, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name)) {
  if (ONLY_COIN && coin !== ONLY_COIN) continue;
  for (const date of fs.readdirSync(path.join(ROOT, coin), { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name).sort()) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;
    if (ONLY_DATE ? date !== ONLY_DATE : date >= today) continue;
    targets.push({ coin, date, dir: path.join(ROOT, coin, date) });
  }
}

const rows = [];
let failures = 0;
for (const { coin, date, dir } of targets) {
  const manifest = readManifest(dir);
  let files;
  if (!manifest && WRITE) {
    files = (await writeManifest(dir, { coin, date, channels: null, source: "verify" })).files;
  } else {
    files = await verifyDayDir(dir);
  }
  const problems = [];
  for (const [name, f] of Object.entries(files)) {
    if (!f.gzip_ok) problems.push(`${name}: gzip ${f.error}`);
    const m = manifest?.files?.[name];
    if (m) {
      if (m.sha256 !== f.sha256) problems.push(`${name}: sha256 与清单不一致`);
      if (m.lines !== f.lines) problems.push(`${name}: 行数 ${f.lines} ≠ 清单 ${m.lines}`);
    }
  }
  if (manifest) {
    for (const name of Object.keys(manifest.files ?? {})) if (!files[name]) problems.push(`${name}: 清单有、文件缺失`);
  }
  const state = problems.length ? "FAIL" : manifest ? "OK" : WRITE ? "WRITTEN" : "NO-MANIFEST";
  if (problems.length) failures += 1;
  const totalLines = Object.values(files).reduce((a, f) => a + f.lines, 0);
  const totalBytes = Object.values(files).reduce((a, f) => a + f.bytes, 0);
  rows.push({ coin, date, state, files: Object.keys(files).length, lines: totalLines, bytes: totalBytes, problems, coverage: coverageOf(manifest) });
}

function coverageOf(manifest) {
  const ch = manifest?.channels;
  if (!ch) return null;
  const l2 = ch.l2book ?? ch.l2full;
  return l2 ? l2.coverage : null;
}

if (JSON_OUT) {
  process.stdout.write(JSON.stringify({ root: ROOT, checked: rows.length, failures, rows }, null, 1) + "\n");
} else {
  process.stdout.write(`${"标的".padEnd(6)}${"日期".padEnd(12)}${"状态".padEnd(13)}${"文件".padStart(4)}${"行数".padStart(10)}${"MB".padStart(8)}${"覆盖".padStart(7)}  问题\n`);
  for (const r of rows) {
    process.stdout.write(
      `${r.coin.padEnd(6)}${r.date.padEnd(12)}${r.state.padEnd(13)}${String(r.files).padStart(4)}${String(r.lines).padStart(10)}` +
        `${(r.bytes / 1e6).toFixed(1).padStart(8)}${(r.coverage == null ? "-" : (r.coverage * 100).toFixed(1) + "%").padStart(7)}  ${r.problems.join("; ")}\n`,
    );
  }
  process.stdout.write(`\n共 ${rows.length} 个日目录，${failures} 个有问题${WRITE ? `（缺清单的已补写 ${MANIFEST_NAME}）` : ""}\n`);
}
process.exit(failures ? 1 : 0);

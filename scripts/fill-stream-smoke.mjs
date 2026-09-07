#!/usr/bin/env node
/**
 * 成交事件流联网冒烟（手动执行，需网络——因此不进 vitest 套件，测试套件禁网络）。
 *
 * 单元测试注入的是假订阅，真正连 Hyperliquid 的那一段（`defaultFillSubscribe`）没有覆盖。
 * 这个脚本验证的就是那一段：传输层能连上、`userFills` 订阅被接受、监听器接线正确、
 * 退订能干净收尾。
 *
 * 只读公开频道：订阅只需要账户地址（公开信息），不需要私钥，也不发送任何交易动作。
 * 默认用零地址——它不会有成交，但订阅建立与快照回执同样能证明链路通。
 *
 * 用法：
 *   npm run build && node scripts/fill-stream-smoke.mjs [--address 0x... --testnet --seconds 20]
 */
import { UserFillStream } from "../lib/index.js";

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

const address = String(args.address ?? "0x0000000000000000000000000000000000000001");
const testnet = args.testnet === "true";
const seconds = Math.max(5, Number(args.seconds ?? 20));

let failed = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "✅" : "❌"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failed += 1;
};

const logger = {
  printInfo: (m) => console.log(`   ${m}`),
  printWarning: (m) => console.log(`   ⚠️ ${m}`),
  printError: (m) => console.log(`   ❌ ${m}`),
};

const hints = [];
const stream = new UserFillStream({
  address,
  testnet,
  logger,
  onFill: (coin) => hints.push(coin),
});

console.log(`订阅 ${testnet ? "测试网" : "主网"} userFills（地址 ${address.slice(0, 6)}…${address.slice(-4)}），观察 ${seconds}s`);
const started = await stream.start();
check("订阅建立", started, stream.health().error ?? "");

if (started) {
  await new Promise((r) => setTimeout(r, seconds * 1000));
  const health = stream.health();
  check("订阅保持", health.subscribed);
  // 快照可能为空（该地址没有成交），收到推送本身就证明订阅被交易所接受
  console.log(`   推送 ${health.events} 次 · 新成交 ${health.fills} 笔 · 去重 ${health.duplicates} 笔 · 提示 ${hints.length} 次`);
  const t0 = Date.now();
  await stream.stop();
  check("退订并释放连接", stream.health().subscribed === false, `${Date.now() - t0}ms`);
}

console.log(failed ? `\n${failed} 项失败` : "\n成交事件流冒烟通过");
process.exit(failed ? 1 : 0);

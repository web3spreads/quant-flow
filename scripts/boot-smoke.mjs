#!/usr/bin/env node
/**
 * 发布前启动冒烟（手动执行，需网络——因此不进 vitest 套件，测试套件禁网络）。
 *
 * 验证链路：真实 @deepseek-ai/cordis Context 挂载插件 → Schema 校验 →
 * apply 装配引擎 → 看板绑定并可访问（页面/总览/配置三接口）→ 配置响应
 * 无私钥泄漏 → fiber.dispose() 优雅卸载（循环停止、端口释放）。
 *
 * 用假私钥运行：只产生对测试网的只读查询，任何签名动作都会被交易所拒绝。
 *
 * 用法：npm run build && node scripts/boot-smoke.mjs
 */
import { Context } from "@deepseek-ai/cordis";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import * as plugin from "../lib/index.js";

process.env.HYPERLIQUID_PRIVATE_KEY ??= "0x" + "1".repeat(64);
process.env.HYPERLIQUID_TESTNET = "true";
const home = fs.mkdtempSync(path.join(os.tmpdir(), "quantflow-smoke-"));
process.chdir(home);

const PORT = 38117;
const ctx = new Context();
const config = new plugin.Config({
  trading: { run_immediately: false },
  web: { port: PORT },
});
const fiber = ctx.plugin(plugin, config);
await new Promise((r) => setTimeout(r, 1500));

let failed = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "✅" : "❌"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failed += 1;
};

const page = await fetch(`http://127.0.0.1:${PORT}/`);
check("看板页面", page.status === 200 && (await page.text()).includes("Quant Flow"));
const overview = await (await fetch(`http://127.0.0.1:${PORT}/api/overview`)).json();
check("总览接口", overview?.engine?.running === true && overview?.engine?.testnet === true);
const cfgText = JSON.stringify(await (await fetch(`http://127.0.0.1:${PORT}/api/config`)).json());
check("配置接口含 Schema", cfgText.includes('"refs"'));
check("配置响应无私钥泄漏", !cfgText.includes(process.env.HYPERLIQUID_PRIVATE_KEY.slice(2)));

const t0 = Date.now();
await fiber.dispose();
const released = await fetch(`http://127.0.0.1:${PORT}/`).then(() => false).catch(() => true);
check("优雅卸载并释放端口", released, `${Date.now() - t0}ms`);

console.log(failed ? `\n${failed} 项失败` : "\n启动冒烟全部通过");
process.exit(failed ? 1 : 0);

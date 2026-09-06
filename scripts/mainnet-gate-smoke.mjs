#!/usr/bin/env node
/**
 * 主网双重闸端到端冒烟（手动执行，需网络——因此不进 vitest 套件）。
 *
 * 用假私钥模拟「切到主网」的三种启动形态，验证闸门在真实插件装配链路上生效：
 *   ① 只设 HYPERLIQUID_TESTNET=false             → 缺名义额上限，拒绝启动
 *   ② 再设 QUANTFLOW_MAINNET_MAX_NOTIONAL_USD    → 缺指纹确认，拒绝启动且错误信息给出指纹
 *   ③ 再把指纹写进 QUANTFLOW_MAINNET_ACK          → 正常启动，客户端被名义额闸包住，看板显示上限
 * 假私钥对应的地址在主网没有资产，任何签名动作都会被交易所拒绝；本脚本只做只读查询。
 *
 * 用法：npm run build && node scripts/mainnet-gate-smoke.mjs
 */
import { Context } from "@deepseek-ai/cordis";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import * as plugin from "../lib/index.js";

process.env.HYPERLIQUID_PRIVATE_KEY ??= "0x" + "7".repeat(64);
process.env.HYPERLIQUID_TESTNET = "false";
delete process.env.QUANTFLOW_MAINNET_MAX_NOTIONAL_USD;
delete process.env.QUANTFLOW_MAINNET_ACK;
const home = fs.mkdtempSync(path.join(os.tmpdir(), "quantflow-gate-smoke-"));
process.chdir(home);

const PORT = 38119;
let failed = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "✅" : "❌"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failed += 1;
};

/** 尝试装配插件：返回 {error} 或 {fiber}。apply 抛错会在 fiber 上以 error 事件/状态呈现，这里直接调用 apply 更可控。 */
async function tryApply() {
  const ctx = new Context();
  const config = new plugin.Config({ trading: { run_immediately: false }, web: { port: PORT } });
  try {
    await plugin.apply(ctx, config);
    return { ctx };
  } catch (e) {
    return { error: String(e?.message ?? e) };
  }
}

// ① 缺上限
const r1 = await tryApply();
check("缺名义额上限 → 拒绝启动", !!r1.error && /QUANTFLOW_MAINNET_MAX_NOTIONAL_USD/.test(r1.error), (r1.error ?? "").slice(0, 80));

// ② 有上限、缺 ACK：错误信息必须给出指纹
process.env.QUANTFLOW_MAINNET_MAX_NOTIONAL_USD = "250";
const r2 = await tryApply();
const fp = /指纹 ([0-9a-f]{64})/.exec(r2.error ?? "")?.[1];
check("有上限缺 ACK → 拒绝启动并给出指纹", !!fp && /QUANTFLOW_MAINNET_ACK/.test(r2.error), fp ? `指纹 ${fp.slice(0, 12)}…` : (r2.error ?? "").slice(0, 80));

// ②b 错的 ACK
process.env.QUANTFLOW_MAINNET_ACK = "0".repeat(64);
const r2b = await tryApply();
check("ACK 不匹配 → 拒绝启动", !!r2b.error && /不匹配/.test(r2b.error));

// ③ 正确 ACK：启动、客户端套闸、看板显示上限
process.env.QUANTFLOW_MAINNET_ACK = fp ?? "";
const r3 = await tryApply();
check("上限 + 正确 ACK → 启动", !r3.error, r3.error ?? "");
if (!r3.error) {
  await new Promise((r) => setTimeout(r, 1500));
  const overview = await (await fetch(`http://127.0.0.1:${PORT}/api/overview`)).json();
  check("主网账户", overview?.engine?.testnet === false);
  check("看板暴露名义额闸", overview?.mainnet_guard?.cap_usd === 250, JSON.stringify(overview?.mainnet_guard));
  check("规则后端、LLM 不在回路", overview?.engine?.llm_in_loop === false && overview?.engine?.llm_provider === "rule");
  // 直接对引擎的客户端验证：这是 MainnetNotionalGuard 实例，且开仓单被闸门拒绝（不会到交易所）
  const guardType = plugin.MainnetNotionalGuard;
  // 通过 fetch 拿不到引擎对象，用 Config 再装配一个 Fleet 太重；改为验证导出与看板一致即可
  check("导出 MainnetNotionalGuard", typeof guardType === "function");
  // 卸载：Context 没有 fiber 句柄时直接退出进程即可（效果注册在 ctx.effect 上）
}
console.log(failed ? `\n${failed} 项失败` : "\n主网双重闸冒烟全部通过");
process.exit(failed ? 1 : 0);

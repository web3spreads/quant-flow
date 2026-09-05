/**
 * dsh-plugin-quant-flow：Quant Flow —— AI 驱动的 Hyperliquid 自动交易系统
 * （DeepSeek Harness / Cordis 插件形态）。
 *
 * 铁律不变：**LLM 只产出结构化 JSON 决策，下单、止盈止损与风控永远由确定性
 * 代码完成。** 策略只有网格一种，由引擎编排并由内置 Web 看板统一呈现与配置。
 *
 * 组合方式（cordis.yml）：
 *   - name: 'dsh-plugin-quant-flow'
 *     config:
 *       trading: { symbols: ['BTC'], grid_enabled: true }
 *
 * AI 能力：默认寄生宿主的 llm 服务（llm.provider: dsh，由 dsh 统一管理供应商
 * 与密钥）；也可切换 llm.provider: openai 直连任意 OpenAI 兼容端点。
 * llm 服务按可选依赖处理（惰性 ctx.get）：服务缺失时策略按既有故障路径降级
 * HOLD/KEEP_GRID 并连续告警，插件本身不因此拒绝启动——风控与持仓维护必须
 * 持续运转，这比决策新鲜度重要。
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Context } from "@deepseek-ai/cordis";
import {
  ConfigSchema,
  deepMerge,
  loadOverrides,
  resolveRuntimeConfig,
  warnUnknownKeys,
  type QuantFlowConfigInput,
  type RuntimeConfig,
} from "./config.js";
import { Fleet, ensureWritableDirs } from "./fleet.js";
import { TradingLogger } from "./logger.js";
import { WebConsole } from "./web/server.js";
import type { DshLlmLike } from "./llm.js";

export const name = "quant-flow";

/** Cordis 配置 Schema（loader 在 apply 前校验；同时是看板配置表单的单一来源）。 */
export const Config = ConfigSchema;

export * from "./config.js";
export { Engine } from "./engine.js";
export { Fleet } from "./fleet.js";
export { TradingLogger } from "./logger.js";
export { LLMClient, LLMError, extractJson, OpenAICompatBackend } from "./llm.js";
export { HyperliquidClient } from "./trading/client.js";
export { OrderManager, LimitOrderMonitor } from "./trading/orderManager.js";
export { GridManager } from "./trading/gridManager.js";
export { GridPnLTracker } from "./trading/gridPnl.js";
export { GridBarrierMonitor, TripleBarrierConfig } from "./trading/gridBarrier.js";
export { GridLevel, GridLevelState, calculateGridConfig, extractOrderId } from "./utils/gridMath.js";
export * from "./plugins/protections/index.js";
export { GridAgent } from "./strategy/gridAgent.js";
export { GridStrategy } from "./strategy/grid.js";
export { TechnicalIndicators, TrendConfirmTracker, detectStrongTrend } from "./data/indicators.js";
export { MarketDataFetcher } from "./data/marketData.js";
export { SimulatedClient, DEFAULT_SIM_ASSETS } from "./sim/simulatedClient.js";
export type { SimAsset } from "./sim/simulatedClient.js";
export { runBacktest, formatReport, buildEngineConfig, RuleGridLlmBackend, TRIGGER_EXIT_PRESET } from "./sim/backtest.js";
export { loadBars, loadFunding, resampleBars, syntheticBars, inferIntervalMs, INTERVAL_MS } from "./sim/dataset.js";
export { clock } from "./utils/clock.js";
export { sleep, installSleep } from "./utils/sleep.js";

export async function apply(ctx: Context, config: QuantFlowConfigInput): Promise<void> {
  const namedLogger = ctx.logger("quant-flow");
  const host = {
    info: (...args: unknown[]) => namedLogger.info(...(args as [unknown])),
    warn: (...args: unknown[]) => namedLogger.warn(...(args as [unknown])),
    error: (...args: unknown[]) => namedLogger.error(...(args as [unknown])),
  };
  const logger = new TradingLogger({ logDir: config.paths.log_dir, host });

  // 配置合成：Schema 校验后的 cordis 基线 < 看板覆盖层
  const baseConfig = config as unknown as Record<string, unknown>;
  warnUnknownKeys(baseConfig, (m) => logger.printWarning(m));
  const overrides = loadOverrides(config.paths.data_dir, (m) => logger.printWarning(m));
  let effectiveInput: QuantFlowConfigInput = config;
  if (Object.keys(overrides).length) {
    // 覆盖层再过一遍 Schema：看板写入的值同样不允许绕过校验
    effectiveInput = new (ConfigSchema as never as new (v: unknown) => QuantFlowConfigInput)(
      deepMerge(baseConfig, overrides),
    );
    logger.printInfo("[配置] 已加载看板覆盖层（data/config.overrides.json）");
  }
  const runtime: RuntimeConfig = resolveRuntimeConfig(effectiveInput);
  ensureWritableDirs(runtime);

  // 惰性解析宿主 llm 服务（可选依赖：缺失时按 LLM 故障降级，不阻塞插件启动）
  const getDshLlm = (): DshLlmLike | undefined =>
    (ctx as unknown as { get?: (name: string) => unknown }).get?.("llm") as DshLlmLike | undefined;

  // 大盘：多账户引擎并行编排（单账户配置=1 个 default 账户）
  const fleet = new Fleet({
    config: runtime,
    host,
    getDshLlm,
  });

  // Web 看板（大盘总控 + 每账户运行/决策/网格数据 + 配置网页设置）
  let web: WebConsole | null = null;
  if (runtime.web.enabled) {
    web = new WebConsole({
      getFleet: () => fleet,
      logger,
      baseConfig,
      dataDir: runtime.paths.data_dir,
      host: runtime.web.host,
      port: runtime.web.port,
      token: runtime.web.token,
      applyConfig: (next) => fleet.applyConfig(next),
      pluginVersion: readOwnVersion(),
    });
    await web.start();
  }

  fleet.start();

  // 生命周期：插件卸载（配置变更/HMR/宿主停机）时优雅停机——
  // 每台引擎等进行中的撤单/布单序列走完，绝不腰斩留下裸仓。
  ctx.effect(() => {
    return async () => {
      await fleet.stop("插件卸载");
      await web?.stop();
    };
  });
}

function readOwnVersion(): string {
  try {
    const here = path.dirname(fileURLToPath(import.meta.url));
    for (const candidate of [path.join(here, "..", "package.json"), path.join(here, "..", "..", "package.json")]) {
      if (fs.existsSync(candidate)) {
        return String(JSON.parse(fs.readFileSync(candidate, "utf-8")).version ?? "0.0.0");
      }
    }
  } catch {
    /* 忽略 */
  }
  return "0.0.0";
}

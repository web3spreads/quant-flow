/**
 * 配置模块：Schemastery Schema（单一事实来源）+ 环境变量 + 看板覆盖层。
 *
 * 设计原则：
 * - 配置能省则省——所有键都有安全默认值，安全机制默认启用；
 * - 敏感信息（私钥、API Key）只从环境变量读取，绝不进配置文件/看板；
 * - Schema 即表单：每个键的中文描述/范围/默认值都在 Schema 上，看板据此
 *   自动渲染配置页（改 Schema = 改表单，永不失同步）。
 *
 * 配置合成顺序：Schema 默认值 < cordis.yml config 块 < 看板覆盖文件
 * （data/config.overrides.json）。覆盖文件由看板写入，重启/热应用后仍生效。
 *
 * 环境变量：
 *     HYPERLIQUID_PRIVATE_KEY      钱包私钥（必填）
 *     HYPERLIQUID_ACCOUNT_ADDRESS  主钱包地址（API 钱包模式选填）
 *     HYPERLIQUID_TESTNET          是否测试网（默认 true，主网需显式设 false）
 *     LLM_API_KEY                  LLM API 密钥（兼容 OPENAI_API_KEY；provider=dsh/rule 时无需）
 *     QUANTFLOW_WEB_TOKEN          看板访问令牌（可选；设置后 API 需携带）
 *     QUANTFLOW_MAINNET_MAX_NOTIONAL_USD  主网名义额硬上限（testnet=false 时必填，>0）
 *     QUANTFLOW_MAINNET_ACK        主网配置指纹确认（testnet=false 时必须等于启动日志打印的指纹）
 *
 * 主网双重闸：测试网与主网只差一个 HYPERLIQUID_TESTNET，因此主网账户额外要求
 * ① 名义额硬上限（引擎下单前检查，超限拒单）；② 生效配置的 SHA-256 指纹确认——
 * 任何影响下单的配置变动都会改变指纹，必须重新确认才能启动。两条缺一即拒绝启动。
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import Schema from "@deepseek-ai/schemastery";
import { privateKeyToAccount } from "viem/accounts";

// ── 各分段 Schema ───────────────────────────────────────────────────────

export interface LLMSection {
  provider: "rule" | "dsh" | "openai";
  dsh_provider: string;
  base_url: string;
  model: string;
  temperature: number;
  timeout: number;
  daily_call_cap: number;
}

export const LLMSchema: Schema<LLMSection> = Schema.object({
  provider: Schema.union([
    Schema.const("rule").description("规则后端：与回测同源，每周期给出 UPDATE_GRID 由重建闸门定夺，LLM 不在交易回路"),
    Schema.const("dsh").description("dsh 宿主 llm 服务（由 DeepSeek Harness 统一管理供应商与密钥）；LLM 在交易回路中"),
    Schema.const("openai").description("自带 OpenAI 兼容客户端（base_url + LLM_API_KEY）；LLM 在交易回路中"),
  ]).default("rule").description(
    "决策来源。默认 rule：规则后端不发任何外部请求、决策可复现；dsh/openai 把 LLM 放进交易回路，" +
    "启动时告警并受 daily_call_cap 约束",
  ),
  dsh_provider: Schema.string().default("deepseek-official").description("provider=dsh 时的适配器路由名（对应 ctx.llm.registerAdapter 的 provider）。dsh 官方 DeepSeek 适配器注册名为 deepseek-official，写错不会报错、只会返回空流"),
  base_url: Schema.string().default("https://api.deepseek.com/v1").description("provider=openai 时的 OpenAI 兼容端点根地址"),
  model: Schema.string().default("deepseek-v4-flash").description("模型名（两种 provider 通用）。provider=dsh 时必须是适配器识别的名字（deepseek-v4-flash / deepseek-v4-pro），别名如 deepseek-chat 只有直连端点认"),
  temperature: Schema.number().min(0).max(2).step(0.05).default(0).description("采样温度（默认 0：同一市场状态得到同一决策，决策可回放、可回测；>0 只会给交易决策加噪声）"),
  timeout: Schema.number().min(5).max(600).default(120).description("单次请求超时（秒）"),
  daily_call_cap: Schema.number().min(1).max(100000).step(1).default(300).description(
    "LLM 每日调用上限（按后端实际请求次数计，重试每次都算；UTC 日）。触顶后当天降级 KEEP_GRID 并告警一次；" +
    "计数持久化在 data/llm-usage.json。provider=rule 时无意义。不可关闭——批量重试曾把共享 key 余额打穿",
  ),
}).description("决策引擎");

export interface TradingSection {
  symbols: string[];
  grid_enabled: boolean;
  max_trade_amount: number;
  max_leverage: number;
  timeframe: string;
  run_immediately: boolean;
}

/** 随永续策略一起移除的 trading 键：出现即提示已忽略，避免用户以为还生效。 */
export const REMOVED_TRADING_KEYS = [
  "perp_enabled", "max_positions", "take_profit_ratio", "stop_loss_ratio", "min_confidence",
  "candles_limit", "timeframe_offset", "min_throttle_secs", "llm_failure_alert_cycles",
];

export const TradingSchema: Schema<TradingSection> = Schema.object({
  symbols: Schema.array(String).default(["BTC"]).description("交易对列表（Hyperliquid 简单符号格式，如 BTC；网格只用第一个）"),
  grid_enabled: Schema.boolean().default(true).description("启用网格策略（数学引擎按波动率布单，LLM 仅低频判形态）。false=不交易，只保留看板与风控只读视图"),
  max_trade_amount: Schema.number().min(1).default(100).description("网格预算回退值（USD）：权益不可得时按此金额布单"),
  max_leverage: Schema.number().min(1).max(50).step(1).default(5).description("杠杆上限"),
  timeframe: Schema.string().default("1h").description("网格形态判断用的 K 线周期（1m/5m/15m/1h/4h/1d）"),
  run_immediately: Schema.boolean().default(true).description("启动时立即执行一轮"),
}).description("交易（账户级参数）");

export interface GridSection {
  interval_minutes: number;
  width_min_pct: number;
  width_max_pct: number;
  width_fallback_pct: number;
  ai_blend_weight: number;
  force_neutral: boolean;
  min_grid_num: number;
  max_grid_num: number;
  capital_ratio: number;
  max_position_notional_usd: number;
  inventory_cap_ratio: number;
  post_only: boolean;
  level_trigger_stop_loss: boolean;
  level_trigger_take_profit: boolean;
  inventory_skew: number;
  trend_side_only: boolean;
  range_filter_er_max: number;
  range_filter_lookback: number;
  halt_below_usd: number;
  trend_filter_enabled: boolean;
  trend_filter_min_votes: number;
  trend_filter_timeframes: string[];
  trend_confirm_cycles: number;
  flatten_adverse: boolean;
  flatten_min_cycles: number;
  llm_failure_alert_cycles: number;
  llm_fallback_rebuild_cycles: number;
  rebuild_cooldown_seconds: number;
  rebuild_min_change_pct: number;
  barrier: Record<string, unknown>;
}

export const GridSchema: Schema<GridSection> = Schema.object({
  interval_minutes: Schema.number().min(1).max(240).step(1).default(5).description("网格决策周期（分钟）"),
  width_min_pct: Schema.number().min(0.001).max(0.5).step(0.001).default(0.02).description("网格宽度下限"),
  width_max_pct: Schema.number().min(0.01).max(1).step(0.01).default(0.15).description("网格宽度上限"),
  width_fallback_pct: Schema.number().min(0.005).max(0.5).step(0.005).default(0.05).description("数据异常时的回退宽度"),
  ai_blend_weight: Schema.percent().default(0.35).description("AI 宽度与市场数据的融合权重"),
  force_neutral: Schema.boolean().default(true).description("强制中性网格（忽略 AI 方向，消除反手亏损）"),
  min_grid_num: Schema.number().min(2).max(20).step(1).default(3).description("自适应仓位最少格数（低于则拒绝布单）"),
  max_grid_num: Schema.number().min(2).max(50).step(1).default(10).description(
    "格数上限（LLM 给出的 grid_num 被钳到此值）。格子越密单格止盈越薄、越难覆盖手续费与库存持有成本，宁疏勿密",
  ),
  capital_ratio: Schema.percent().default(0.5).description(
    "网格保证金预算 = 账户权益 × 此比例（再 × 杠杆 × 0.4 安全系数 = 总名义额）。0=改用固定预算：以 trading.max_trade_amount 为上限，与权益无关",
  ),
  max_position_notional_usd: Schema.number().min(0).default(0).description("库存硬上限（USD 名义额）。0=自动：按 inventory_cap_ratio 由本代网格单侧名义额推导"),
  inventory_cap_ratio: Schema.number().min(0).max(10).step(0.05).default(0.7).description(
    "自动库存上限 = 本代网格单侧名义额 × 此倍数（max_position_notional_usd=0 时生效；0=不限，不建议）。" +
    "这是收益的主要杠杆：网格靠价差赚钱、靠方向性库存亏钱，收紧上限按比例砍掉后者而对前者影响小得多。" +
    "太松被趋势吃掉、太紧连价差也赚不到，呈倒 U 型；最优点不跨品种/周期迁移，换标的必须重扫",
  ),
  post_only: Schema.boolean().default(true).description("网格挂单只做 maker（Alo）：会立即成交的单被拒而不追价，杜绝限价单穿价变 taker（历史 taker 成交 94% 来自此）"),
  level_trigger_stop_loss: Schema.boolean().default(false).description(
    "每个层级成交后再挂一张止损触发单。止损距离 2×tp 小于网格间距×1.5，趋势中一边接货一边以 taker 割上一层；网格的库存风险由层数×单格×库存上限与网格级屏障界定，不建议开",
  ),
  level_trigger_take_profit: Schema.boolean().default(false).description(
    "每个层级成交后再挂一张与限价平仓单同价的止盈触发单。触发后以 taker 成交抢在 maker 平仓单之前，每轮费用翻倍，不建议开",
  ),
  inventory_skew: Schema.number().min(0).max(1).step(0.05).default(0).description(
    "库存倾斜报价强度（0=关，对称中性网格）。持多时把整张网格下移、持空时上移，位移 = 强度 × 库存占上限比例 × 半宽——" +
    "越接近库存上限，加仓侧越远离市价、减仓侧越贴近，让库存自然收敛而不靠市价砍仓。需回测验证后再开",
  ),
  trend_side_only: Schema.boolean().default(true).description(
    "确认强趋势时只挂顺势开仓单（上涨只挂买、下跌只挂卖），逆势侧的未成交开仓单撤掉、层级复位；" +
    "为 false 时确认强趋势即全面暂停加仓。中性网格在单边行情里必然累积逆势库存，这是把它改成顺势阶梯的开关",
  ),
  range_filter_er_max: Schema.number().min(0).max(1).step(0.01).default(0).description(
    "形态闸门：效率比（净位移/路程）高于此值即判为单边行情，本轮不挂任何新开仓单并撤掉未成交的开仓单（已成交层级的 reduce_only 平仓单照常维持）。" +
    "0=关闭。单边行情里中性网格会持续累积逆势库存，这道闸门用于把这类行情挡在门外；阈值需按品种回测确定",
  ),
  range_filter_lookback: Schema.number().min(10).max(500).step(1).default(96).description("效率比回看根数（按 trading.timeframe 计；96 根 1h ≈ 4 天）"),
  halt_below_usd: Schema.number().min(0).default(0).description("净值停机线（低于此值且无持仓跳过周期，0=关闭）"),
  trend_filter_enabled: Schema.boolean().default(true).description("多周期强势一致时暂停加仓"),
  trend_filter_min_votes: Schema.number().min(1).max(5).step(1).default(3).description("强势周期票数阈值"),
  trend_filter_timeframes: Schema.array(String).default(["15m", "1h", "4h", "1d"]).description("参与趋势计票的周期白名单（排除 1m 等噪声周期）"),
  trend_confirm_cycles: Schema.number().min(1).max(12).step(1).default(2).description("连续 N 周期同向确认才暂停（迟滞去抖）"),
  flatten_adverse: Schema.boolean().default(true).description("强趋势中减掉逆势库存"),
  flatten_min_cycles: Schema.number().min(1).max(24).step(1).default(3).description("平逆势库存需更多连续确认（暂停先行、平仓靠后）"),
  llm_failure_alert_cycles: Schema.number().min(0).step(1).default(6).description("网格 LLM 连续失败 N 周期告警（0=关闭）"),
  llm_fallback_rebuild_cycles: Schema.number().min(0).step(1).default(12).description("空转 N 周期后纯市场数据兜底重建（0=关闭）"),
  rebuild_cooldown_seconds: Schema.number().min(0).step(60).default(3600).description("全量重建冷却（秒，0=关闭；价格真突破旧区间 0.5% 时自动提前解除）"),
  rebuild_min_change_pct: Schema.number().min(0).max(0.5).step(0.001).default(0.01).description("区间变化低于此比例不重建"),
  barrier: Schema.dict(Schema.any()).default({}).description(
    "Triple Barrier 覆盖项（默认：止损 -5% / 止盈 +10%；时限与追踪止损默认关——两者都是按时间/回撤把整张网格的库存市价倒掉，与网格「持库存等回归」的盈利机制正面对抗）。" +
    "可用键：stop_loss_pct / take_profit_pct / time_limit_seconds / trailing_stop_activation_pct / trailing_stop_delta_pct / price_lower_limit / price_upper_limit（值 null=关闭）",
  ),
}).description("网格策略（安全机制全部默认启用）");

export interface WebSection {
  enabled: boolean;
  host: string;
  port: number;
}

export const WebSchema: Schema<WebSection> = Schema.object({
  enabled: Schema.boolean().default(true).description("启用内置看板（运行数据/决策记录/配置管理）"),
  host: Schema.string().default("127.0.0.1").description("看板监听地址（暴露公网前务必设置 QUANTFLOW_WEB_TOKEN）"),
  port: Schema.number().min(1).max(65535).step(1).default(3181).description("看板监听端口"),
}).description("内置 Web 看板");

export interface FleetSection {
  start_stagger_secs: number;
}

export const FleetSchema: Schema<FleetSection> = Schema.object({
  start_stagger_secs: Schema.number().min(0).max(60).default(2).description(
    "多账户启动错峰间隔（秒，0=同时启动）。N 台引擎共用一个出口 IP，齐射请求会撞" +
    "交易所限流；错峰让各账户的网格间隔循环永久错开相位。账户数量本身不设上限",
  ),
}).description("大盘（多账户编排）");

export interface PathsSection {
  data_dir: string;
  log_dir: string;
}

export const PathsSchema: Schema<PathsSection> = Schema.object({
  data_dir: Schema.string().default("data").description("状态文件目录（grid_state.json、保护插件状态、看板配置覆盖）"),
  log_dir: Schema.string().default("logs").description("日志目录（main.log 与 decisions/trades/equity JSONL）"),
}).description("数据与日志路径");

// 未配置 protections 段时的默认保护链（显式配置 protections: [] 可全部关闭）
export const DEFAULT_PROTECTIONS: Record<string, unknown>[] = [
  { name: "max_drawdown", max_drawdown_pct: 0.10, pause_hours: 4 },
  { name: "daily_loss", max_daily_loss_pct: 0.05, pause_hours: 4 },
  { name: "consecutive_loss", max_consecutive_losses: 5, per_symbol: true, pause_hours: 4 },
];

export interface AccountSection {
  name: string;
  private_key_env: string;
  account_address: string;
  testnet: boolean;
  mainnet_max_notional_env: string;
  mainnet_ack_env: string;
  llm: Record<string, unknown>;
  trading: Record<string, unknown>;
  grid: Record<string, unknown>;
  protections: Record<string, unknown>[] | null;
}

/**
 * 多账户条目：每个账户是一套独立的「地址 × 环境 × 交易对」组合，并行运行。
 *
 * - 私钥仍只走环境变量：private_key_env 指定读哪个变量（多地址=多个变量）；
 * - testnet 按账户配：true=测试网（模拟盘），false=主网（实盘），可任意混跑；
 * - llm/trading/grid/protections 是**对顶层同名段的局部覆盖**（deepMerge 后
 *   再过 Schema 校验），顶层段即全体账户的默认模板——只写差异即可。
 */
export const AccountSchema: Schema<AccountSection> = Schema.object({
  name: Schema.string().required().description("账户名（看板标识与 data/logs 子目录名，需唯一）"),
  private_key_env: Schema.string().default("HYPERLIQUID_PRIVATE_KEY").description("私钥所在的环境变量名（密钥本身绝不入配置）"),
  account_address: Schema.string().default("").description("API 钱包模式的主钱包地址（公开信息；空=单钱包模式）"),
  testnet: Schema.boolean().default(true).description("true=测试网（模拟盘）；false=主网（实盘，需满足主网双重闸）"),
  mainnet_max_notional_env: Schema.string().default("QUANTFLOW_MAINNET_MAX_NOTIONAL_USD").description(
    "主网名义额硬上限所在的环境变量名（testnet=false 时必填且 >0；多个主网账户可共用）",
  ),
  mainnet_ack_env: Schema.string().default("QUANTFLOW_MAINNET_ACK").description(
    "主网配置指纹确认所在的环境变量名（testnet=false 时必须等于本账户指纹；每个主网账户指纹不同，须各配一个变量）",
  ),
  llm: Schema.dict(Schema.any()).default({}).description("对顶层 llm 段的局部覆盖（只写差异）"),
  trading: Schema.dict(Schema.any()).default({}).description("对顶层 trading 段的局部覆盖（如本账户换交易对：{symbols: [ETH]}）"),
  grid: Schema.dict(Schema.any()).default({}).description("对顶层 grid 段的局部覆盖"),
  protections: Schema.union([Schema.array(Schema.dict(Schema.any())), Schema.const(null)])
    .default(null)
    .description("账户级保护链（null=继承顶层；[]=本账户全关）"),
}).description("账户条目");

export interface QuantFlowConfigInput {
  llm: LLMSection;
  trading: TradingSection;
  grid: GridSection;
  web: WebSection;
  fleet: FleetSection;
  paths: PathsSection;
  protections?: Record<string, unknown>[] | null;
  accounts: AccountSection[];
}

/** 插件根 Schema（导出名 Config 供 Cordis 校验，见 index.ts）。 */
export const ConfigSchema: Schema<QuantFlowConfigInput> = Schema.object({
  llm: LLMSchema,
  trading: TradingSchema,
  grid: GridSchema,
  web: WebSchema,
  fleet: FleetSchema,
  paths: PathsSchema,
  protections: Schema.union([Schema.array(Schema.dict(Schema.any())), Schema.const(null)])
    .default(null)
    .description("账户保护链（null/缺省=默认链：回撤全平+日亏暂停+连亏锁定+超时强平；[] 全关，不建议）"),
  accounts: Schema.array(AccountSchema).default([]).description(
    "多账户并行（空=单账户模式，走顶层配置与 HYPERLIQUID_* 环境变量）。" +
    "每条目=一套「地址×环境×策略」组合，各自独立引擎/状态/日志/保护链并行运行，大盘页总控",
  ),
}).description("Quant Flow 配置");

// ── 环境变量派生段（不进 Schema：敏感信息绝不可视化/落盘） ──────────────

export interface ExchangeSection {
  private_key: string;
  account_address: string | null;
  testnet: boolean;
  /** 主网名义额硬上限（USD）；测试网恒 0（不设闸） */
  mainnet_max_notional_usd: number;
}

/** 单账户模式的主网闸环境变量名（多账户模式按条目的 *_env 字段指定）。 */
export const MAINNET_MAX_NOTIONAL_ENV = "QUANTFLOW_MAINNET_MAX_NOTIONAL_USD";
export const MAINNET_ACK_ENV = "QUANTFLOW_MAINNET_ACK";

/** 解析布尔环境变量：true/1/yes 为真（大小写不敏感），未设置时用默认值。 */
export function envBool(name: string, defaultValue: boolean): boolean {
  const raw = process.env[name];
  if (raw === undefined || !raw.trim()) return defaultValue;
  return ["true", "1", "yes"].includes(raw.trim().toLowerCase());
}

/** 从环境变量加载交易所配置；缺少私钥时抛错（插件加载失败，绝不带病启动）。 */
export function loadExchangeFromEnv(): ExchangeSection {
  const privateKey = (process.env.HYPERLIQUID_PRIVATE_KEY ?? "").trim();
  if (!privateKey) {
    throw new Error("缺少环境变量 HYPERLIQUID_PRIVATE_KEY，请在宿主环境（或 .env）中配置");
  }
  return {
    private_key: privateKey,
    account_address: (process.env.HYPERLIQUID_ACCOUNT_ADDRESS ?? "").trim() || null,
    testnet: envBool("HYPERLIQUID_TESTNET", true),
    mainnet_max_notional_usd: 0,
  };
}

// ── 主网双重闸 ──────────────────────────────────────────────────────────

/** 递归按键排序的规范化 JSON（指纹输入必须与对象构造顺序无关）。 */
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const entries = Object.keys(value as Record<string, unknown>)
      .sort()
      .filter((k) => (value as Record<string, unknown>)[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${canonicalJson((value as Record<string, unknown>)[k])}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value);
}

/**
 * 账户生效配置的指纹（SHA-256 hex）。
 *
 * 覆盖所有影响下单的配置：交易对与杠杆、网格参数、保护链、决策来源、环境、
 * 名义额上限，以及（主网）由私钥推导的钱包地址——换钥匙也必须重新确认。
 * 不含私钥、API Key（密钥绝不进入任何可打印的材料）与路径/看板等运维项。
 */
export function configFingerprint(account: EngineConfig): string {
  const { api_key: _apiKey, ...llm } = account.llm;
  let wallet: string | null = null;
  if (!account.exchange.testnet) {
    let pk = account.exchange.private_key;
    if (!pk.startsWith("0x")) pk = "0x" + pk;
    try {
      wallet = privateKeyToAccount(pk as `0x${string}`).address.toLowerCase();
    } catch (e) {
      throw new Error(`主网账户 ${account.name} 的私钥无法解析（${e}），拒绝启动`);
    }
  }
  const payload = {
    name: account.name,
    exchange: {
      testnet: account.exchange.testnet,
      account_address: account.exchange.account_address,
      mainnet_max_notional_usd: account.exchange.mainnet_max_notional_usd,
      wallet,
    },
    llm,
    trading: account.trading,
    grid: account.grid,
    protections: account.protections,
  };
  return crypto.createHash("sha256").update(canonicalJson(payload)).digest("hex");
}

/**
 * 主网双重闸（testnet=false 的账户逐个执行）：
 * ① 名义额硬上限环境变量必须存在且 >0，写入 exchange.mainnet_max_notional_usd；
 * ② 生效配置指纹必须与确认环境变量完全相等。
 * 任一不满足即抛错拒绝启动/拒绝热重配。指纹随错误信息打印，供运维核对后写入确认变量。
 */
function applyMainnetGates(account: EngineConfig, capEnv: string, ackEnv: string): void {
  if (account.exchange.testnet) return;
  const rawCap = (process.env[capEnv] ?? "").trim();
  const cap = Number(rawCap);
  if (!rawCap || !Number.isFinite(cap) || cap <= 0) {
    throw new Error(
      `主网账户 ${account.name} 缺少环境变量 ${capEnv}（>0 的美元名义额硬上限），拒绝启动——主网与测试网只差一个变量，硬上限是第一道闸`,
    );
  }
  account.exchange.mainnet_max_notional_usd = cap;
  const fingerprint = configFingerprint(account);
  const ack = (process.env[ackEnv] ?? "").trim().toLowerCase();
  if (ack !== fingerprint) {
    throw new Error(
      `主网账户 ${account.name} 配置指纹 ${fingerprint}，环境变量 ${ackEnv} ${ack ? "与之不匹配" : "未设置"}，拒绝启动——` +
        "核对生效配置后把该指纹写入确认变量再启动；任何影响下单的配置变动都会改变指纹",
    );
  }
}

// ── 运行时配置（Schema 输出 + 归一化 + 环境段） ─────────────────────────

/** 单账户的引擎级配置（Engine 消费的完整视图）。 */
export interface EngineConfig {
  /** 账户名（看板标识与目录名） */
  name: string;
  llm: LLMSection & { api_key: string };
  exchange: ExchangeSection;
  trading: TradingSection & { symbols: string[] };
  grid: GridSection;
  /** 本账户的独立数据/日志目录（多账户时为 <根>/accounts/<name>） */
  paths: PathsSection;
  protections: Record<string, unknown>[];
}

export interface RuntimeConfig {
  /** 并行账户列表（数量不限；单账户模式=1 个名为 default 的账户） */
  accounts: EngineConfig[];
  web: WebSection & { token: string };
  fleet: FleetSection;
  paths: PathsSection;
}

function normalizeTrading(trading: TradingSection): TradingSection & { symbols: string[] } {
  // symbols 误写成标量字符串（symbols: BTC）时按单交易对纠偏，而非逐字符拆解
  // 成 ("B","T","C") 静默产生三个非法交易对
  const rawSymbols: unknown = (trading as { symbols: unknown }).symbols ?? ["BTC"];
  const symbolList = typeof rawSymbols === "string" ? [rawSymbols] : (rawSymbols as unknown[]);
  const symbols = symbolList.map((s) => String(s).toUpperCase()).filter((s) => s.length > 0);
  if (symbols.length === 0) throw new Error("trading.symbols 不能为空");
  return { ...trading, symbols };
}

function normalizeGrid(grid: GridSection): GridSection {
  // trend_filter_timeframes 误写标量（"15m"）按单元素处理，而非拆成 ("1","5","m")
  const rawTf: unknown = (grid as { trend_filter_timeframes: unknown }).trend_filter_timeframes;
  const timeframes = typeof rawTf === "string" ? [rawTf] : ((rawTf as unknown[]) ?? []).map(String);
  return { ...grid, trend_filter_timeframes: timeframes };
}

function resolveProtections(value: Record<string, unknown>[] | null | undefined): Record<string, unknown>[] {
  return value == null ? DEFAULT_PROTECTIONS.map((p) => ({ ...p })) : value.map((p) => ({ ...p }));
}

const ACCOUNT_NAME_PATTERN = /^[A-Za-z0-9_-]{1,32}$/;

/**
 * 把 Schema 校验后的配置归一为运行时配置（多账户展开）。
 *
 * 单账户模式（accounts 为空）：合成一个名为 "default" 的账户，交易所配置来自
 * HYPERLIQUID_* 环境变量，数据/日志路径不带 accounts/ 前缀。
 *
 * 多账户模式：每条目的 llm/trading/grid 与顶层同名段 deepMerge（顶层=模板，
 * 条目=差异）后**再过对应 Schema 校验**（覆盖值同样不许绕过校验）；私钥按
 * private_key_env 指定的环境变量读取，缺失时报错点名变量；数据/日志目录
 * 隔离到 <根>/accounts/<name>/。账户名冲突直接拒绝——两个引擎共写一份
 * grid_state.json 是簿记灾难。
 */
export function resolveRuntimeConfig(input: QuantFlowConfigInput, exchange?: ExchangeSection): RuntimeConfig {
  const web = { ...input.web, token: (process.env.QUANTFLOW_WEB_TOKEN ?? "").trim() };
  const basePaths = input.paths;
  const baseLlm = { ...input.llm, api_key: process.env.LLM_API_KEY || process.env.OPENAI_API_KEY || "" };

  const accounts: EngineConfig[] = [];
  if (!input.accounts?.length) {
    // 单账户模式：路径不带 accounts/ 前缀
    const account: EngineConfig = {
      name: "default",
      llm: baseLlm,
      exchange: { ...(exchange ?? loadExchangeFromEnv()) },
      trading: normalizeTrading(input.trading),
      grid: normalizeGrid(input.grid),
      paths: basePaths,
      protections: resolveProtections(input.protections),
    };
    applyMainnetGates(account, MAINNET_MAX_NOTIONAL_ENV, MAINNET_ACK_ENV);
    accounts.push(account);
  } else {
    const seen = new Set<string>();
    for (const account of input.accounts) {
      const name = String(account.name ?? "").trim();
      if (!ACCOUNT_NAME_PATTERN.test(name)) {
        throw new Error(`账户名 ${JSON.stringify(account.name)} 非法（限 1-32 位字母/数字/_/-，用作目录名）`);
      }
      if (seen.has(name)) {
        throw new Error(`账户名重复: ${name}——两个引擎共写同一份状态文件是簿记灾难，拒绝启动`);
      }
      seen.add(name);

      const privateKeyEnv = account.private_key_env || "HYPERLIQUID_PRIVATE_KEY";
      const privateKey = (process.env[privateKeyEnv] ?? "").trim();
      if (!privateKey) {
        throw new Error(`账户 ${name} 缺少私钥：环境变量 ${privateKeyEnv} 未设置`);
      }

      // 条目段与顶层模板合并后再过 Schema：覆盖值不许绕过校验与默认值填充
      const llmMerged = new (LLMSchema as never as new (v: unknown) => LLMSection)(
        deepMerge({ ...input.llm } as Record<string, unknown>, account.llm),
      );
      const tradingMerged = new (TradingSchema as never as new (v: unknown) => TradingSection)(
        deepMerge({ ...input.trading } as Record<string, unknown>, account.trading),
      );
      const gridMerged = new (GridSchema as never as new (v: unknown) => GridSection)(
        deepMerge({ ...input.grid } as Record<string, unknown>, account.grid),
      );

      const engineConfig: EngineConfig = {
        name,
        llm: { ...llmMerged, api_key: baseLlm.api_key },
        exchange: {
          private_key: privateKey,
          account_address: String(account.account_address ?? "").trim() || null,
          testnet: account.testnet !== false,
          mainnet_max_notional_usd: 0,
        },
        trading: normalizeTrading(tradingMerged),
        grid: normalizeGrid(gridMerged),
        paths: {
          data_dir: `${basePaths.data_dir}/accounts/${name}`,
          log_dir: `${basePaths.log_dir}/accounts/${name}`,
        },
        protections: resolveProtections(account.protections ?? input.protections),
      };
      applyMainnetGates(
        engineConfig,
        String(account.mainnet_max_notional_env || MAINNET_MAX_NOTIONAL_ENV).trim(),
        String(account.mainnet_ack_env || MAINNET_ACK_ENV).trim(),
      );
      accounts.push(engineConfig);
    }
  }

  return { accounts, web, fleet: { ...input.fleet }, paths: basePaths };
}

// ── 未知配置键告警 ──────────────────────────────────────────────────────

/**
 * 逐条告警未知键。Schema 会静默忽略它们，而一个拼错的键意味着库存上限、
 * 停机线这类安全阀以为设了、其实没设。仅告警不报错，保持「配置能省则省」。
 */
export function warnUnknownKeys(
  raw: Record<string, unknown>,
  warn: (message: string) => void,
): void {
  const knownSections = new Set(["llm", "trading", "grid", "web", "fleet", "paths", "protections", "accounts"]);
  for (const key of Object.keys(raw)) {
    if (!knownSections.has(key)) {
      warn(`[配置] 未知的顶层配置段 '${key}' 已被忽略，请核对拼写`);
    }
  }
  const sectionKnown: Record<string, Set<string>> = {
    fleet: new Set(Object.keys(FleetSchema.dict ?? {})),
    llm: new Set(Object.keys(LLMSchema.dict ?? {})),
    trading: new Set(Object.keys(TradingSchema.dict ?? {})),
    grid: new Set(Object.keys(GridSchema.dict ?? {})),
    web: new Set(Object.keys(WebSchema.dict ?? {})),
    paths: new Set(Object.keys(PathsSchema.dict ?? {})),
  };
  for (const [section, known] of Object.entries(sectionKnown)) {
    const data = raw[section];
    if (!data || typeof data !== "object") continue;
    for (const key of Object.keys(data as Record<string, unknown>)) {
      if (known.has(key)) continue;
      if (section === "llm" && key === "api_key") {
        warn("[配置] llm.api_key 不从配置读取，请改用环境变量 LLM_API_KEY");
      } else if (section === "trading" && REMOVED_TRADING_KEYS.includes(key)) {
        warn(`[配置] trading.${key} 属于已移除的永续策略，当前值已被忽略`);
      } else {
        warn(`[配置] 未知的 ${section}.${key} 已被忽略，请核对拼写`);
      }
    }
  }
}

// ── 看板覆盖层 ──────────────────────────────────────────────────────────

export const OVERRIDES_FILENAME = "config.overrides.json";

/** 深合并（对象递归、数组整体替换——数组是配置语义的原子值，逐项合并会拼出四不像）。 */
export function deepMerge<T>(base: T, patch: unknown): T {
  if (patch === undefined) return base;
  if (
    base !== null && typeof base === "object" && !Array.isArray(base) &&
    patch !== null && typeof patch === "object" && !Array.isArray(patch)
  ) {
    const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
    for (const [key, value] of Object.entries(patch as Record<string, unknown>)) {
      out[key] = key in out ? deepMerge(out[key], value) : value;
    }
    return out as T;
  }
  return patch as T;
}

/** 读取看板覆盖文件（不存在/损坏返回空对象——覆盖层丢失只是回到基线配置，不致命，但要告警）。 */
export function loadOverrides(dataDir: string, warn?: (m: string) => void): Record<string, unknown> {
  const file = path.join(dataDir, OVERRIDES_FILENAME);
  try {
    if (!fs.existsSync(file)) return {};
    const parsed = JSON.parse(fs.readFileSync(file, "utf-8"));
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
    warn?.(`[配置] 覆盖文件 ${OVERRIDES_FILENAME} 内容不是对象，已忽略`);
    return {};
  } catch (e) {
    warn?.(`[配置] 覆盖文件 ${OVERRIDES_FILENAME} 读取失败（已忽略，回到基线配置）: ${e}`);
    return {};
  }
}

/** 原子写入覆盖文件（tempfile + rename，防进程中断截断）。 */
export function saveOverrides(dataDir: string, overrides: Record<string, unknown>): void {
  fs.mkdirSync(dataDir, { recursive: true });
  const file = path.join(dataDir, OVERRIDES_FILENAME);
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(overrides, null, 2), "utf-8");
  fs.renameSync(tmp, file);
}

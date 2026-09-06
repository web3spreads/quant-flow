# CLAUDE.md

## 默认语言为中文

文档、代码注释、commit、PR/Issue、日志输出与 AI 交互使用中文。
例外：标识符与技术术语；commit message。

## 项目

Quant Flow：AI 驱动的 Hyperliquid 网格交易系统，形态为 **dsh（DeepSeek Harness）的
Cordis 插件**（`dsh-plugin-quant-flow`，TypeScript/ESM）。铁律：**LLM 只产出结构化 JSON
决策，下单、止盈止损与风控永远由确定性代码完成。**

**策略只有网格一种**（`src/strategy/grid.ts`），由 `src/engine.ts` 编排；`src/fleet.ts`
把多账户装配成并行引擎并提供大盘聚合，账户间零共享可变状态，同「地址×环境」双开拒绝启动。
决策来源默认是**规则后端**（`llm.provider: rule`，与回测同源，LLM 不在交易回路）；可切宿主
`ctx.llm`（dsh）或 OpenAI 兼容端点，此时启动即告警并受 `llm.daily_call_cap` 约束（按每次实际
请求计数、持久化到 `data/llm-usage.json`，触顶当天降级 KEEP_GRID）。内置看板提供运行数据、
决策记录与全部配置的网页设置（表单由 `src/config.ts` 的 Schema 自动生成，改 Schema 即改表单）。

**主网双重闸**：`testnet=false` 的账户必须同时提供名义额硬上限（`QUANTFLOW_MAINNET_MAX_NOTIONAL_USD`，
`src/trading/notionalGuard.ts` 在客户端层拦所有开仓单，reduce_only 不拦、查询失败 fail-closed）与
生效配置指纹确认（`QUANTFLOW_MAINNET_ACK`），两者在 `resolveRuntimeConfig` 校验——启动与看板热重配
走同一入口，改了影响下单的配置就必须重新确认。

架构见 `docs/architecture.md`。

## 命令

```bash
npm run check && npm test                 # 改动后必跑
npm run build                             # 编译到 lib/
node scripts/fetch-history.mjs --coin BTC --interval 15m
node scripts/backtest-grid.mjs --symbol BTC --interval 15m --compare   # 需先 build
node scripts/backtest-suite.mjs           # 整批回测，结果进看板「回测」页
node scripts/attribution.mjs --testnet    # 链上成交归因（只读）
node scripts/record-book.mjs --coins BTC,ETH --out data/book           # 盘口录制（日切写 manifest.json）
node scripts/book-verify.mjs --dir data/book                           # 盘口数据完整性校验
RAW=… PARQUET=… ./research/run.sh                                     # 研究管线（Python，见 research/README.md）
```

**改网格或执行层前先跑回测对照**（`--compare`、`--sweep`），用数字说话。

## 设计原则

1. LLM 故障（解析失败、非法 action、调用异常）一律降级 KEEP_GRID，绝不透传执行层；
   兜底决策必须打 `llm_ok=false`。**交易所接口故障不算 LLM 故障**，别污染连败告警
2. 所有配置键有内置默认值，安全机制默认启用；新功能不得要求用户新增配置才获得安全行为；
   新增键必须带中文 description
3. 账户级保护实现 `IProtection` 并注册 `PROTECTION_REGISTRY`；风控强平不上报虚假 pnl；
   **只有确认平仓成功才清理保护插件的持仓记录**
4. 状态文件原子写入（tmp + rename）；核心计算用 Decimal，仅 API 边界转 number
5. 资金安全机制必须带重试且失败路径有日志（止损单失败 → 立即回滚平仓）
6. **查询失败（null）与确认为空（[]）是不同的风控语义，绝不混用**——把「查不到」当
   「没有」会撤掉在保护真实持仓的止损单、把成交误判为撤销
7. 「现在几点」只走 `utils/clock.ts`，等待只走 `utils/sleep.ts`，交易所访问只走
   `ExchangeClientLike`——回测同源依赖这三条，直接 `Date.now()`/`setTimeout`/
   `new HyperliquidClient` 会让代码在模拟器里失效
8. 单向持仓的两条网格硬规则：持有在途层级不重心化；开仓单不挂进库存亏损区
9. 网格靠价差赚钱、靠方向性库存亏钱：**库存额度（`grid.inventory_cap_ratio`）是收益的
   主要杠杆**，呈倒 U 型。调宽度/格数/屏障之前先扫这个
10. 新方向先做**可证伪的最小实验**再投工程：用同源回测跑数字，样本要跨标的跨周期，
    `|t| < 2` 一律视为与零无异；单点最优而邻域崩塌的参数是过拟合，不是杠杆。
    盘口录制器已在生产机常驻，2026-10 初攒满一个月做秒级微观结构研究

## 规范与注意

- 测试禁网络与真实密钥：LLM 用 `tests/support.ts` 的 FakeLLMBackend，交易所用
  FakeGridClient / FakeOrderManager；`checkOrderSuccess` 等校验逻辑复用真实实现
- **测试不追求一函数一用例**：同一条不变量的多个分支合并成表驱动用例；只保留「错了会
  赔钱或掩盖故障」的断言。新增测试前先想清楚它防的是哪次事故
- Hyperliquid：简单符号格式（`BTC`）；持仓 `szi` 为带符号字符串；`HYPERLIQUID_TESTNET`
  切网（默认测试网）；**HL 拒单时外层仍 `status=ok`、错误藏在 `statuses[].error`**，
  只判外层会把被拒单记成成功；exchange 动作用资产索引
- 状态文件 `data/grid_state.json` 原子写入，进程重启据此恢复层级与在途订单
- **任何批量 LLM 调用不得与生产共用 API key**：另配带额度上限的 key，跑前先算预算
  （调用数 × 单次 token × 重试放大），并在连续失败时中止而不是继续重试
- `.env`、`logs/`、`data/`、`lib/`、`node_modules/` 不入库；`cordis.patch.yml` 属于仓库内容

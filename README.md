# Quant Flow

AI 驱动的 [Hyperliquid](https://hyperliquid.xyz) 网格交易系统，形态为[DeepSeek Harness（dsh）](https://github.com/deepseek-ai/deepseek-harness)插件。**LLM 只产出结构化 JSON 决策，下单、止盈止损与风控永远由确定性代码完成**，LLM 故障绝不放大成交易动作。

**策略只有网格一种**：LLM 低频判形态（要不要更新、往哪个方向、多宽），数学引擎按波动率与账户实际费率算价位与金额，GridManager 管理层级生命周期——批量 post-only 布单、开→平闭环、持库存不重心化、库存守卫与自动库存上限、Triple Barrier 兜底。

**多账户并行**：`accounts` 配置任意数量的「地址 × 环境 × 交易对」组合同时运行，数量不设上限；每账户独立引擎、私钥环境变量、状态与日志目录、保护链，互不干扰（同地址同环境双开拒绝启动）。按 `fleet.start_stagger_secs`（默认 2s）错峰启动，避免 N 台引擎对同一出口 IP 齐射撞限流。

**内置看板**（默认 http://127.0.0.1:3181/ ）：大盘对比、每账户运行数据与决策时间线、网格层级阶梯、回测报告，以及全部配置的网页设置（表单由 Schema 自动生成，保存后热重配）。

## 使用

```bash
export HYPERLIQUID_PRIVATE_KEY=0x...       # 必填，交易钱包或 API 钱包私钥
export HYPERLIQUID_TESTNET=true            # 默认测试网；主网须显式 false

dsh plugin --profile trading add dsh-plugin-quant-flow
dsh --profile trading                      # 看板在 http://127.0.0.1:3181/
```

其它环境变量：`HYPERLIQUID_ACCOUNT_ADDRESS`（仅 API 钱包模式）、`LLM_API_KEY`（仅 `llm.provider=openai` 时）、`QUANTFLOW_WEB_TOKEN`（看板监听非 127.0.0.1 时必设）。

配置三层叠加，全部键都有安全默认值，能省则省：**Schema 内置默认值**（`src/config.ts`，权威清单，也是看板表单的来源）<**profile 的 `cordis.patch.yml`** < **看板「配置」页**（改动经 Schema 校验后原子落盘`data/config.overrides.json` 并触发热重配）。

```yaml
- insert:
    - id: quant-flow
      name: dsh-plugin-quant-flow
      config:
        trading: { symbols: [BTC] }          # 顶层段=全体账户的模板
        accounts:                            # 省略则为单账户模式
          - { name: grid-bot, private_key_env: PK_GRID, testnet: true,
              trading: { symbols: [ETH] } }
          - { name: live,     private_key_env: PK_LIVE, testnet: false,
              trading: { max_leverage: 2 } }
```

拼错或已移除的键会被忽略，并在启动日志打 `[配置]` 告警——安全阀不会「以为设了其实没设」。

## 开发

```bash
npm ci && npm run check && npm test        # 类型检查 + vitest
npm run build                              # 编译到 lib/
node scripts/fetch-history.mjs --coin BTC --interval 15m    # 拉历史 K 线
node scripts/backtest-grid.mjs --symbol BTC --interval 15m --compare
node scripts/backtest-suite.mjs            # 整批回测，结果进看板「回测」页
node scripts/attribution.mjs --testnet     # 链上成交归因（只读）
```

**改网格或执行层之前先跑回测对照**，用数字说话；回测用的是生产同一套引擎，撮合偏保守，数字用于比较方案而非预测收益。发布前自测：`node scripts/boot-smoke.mjs`（单账户启动链路）、`node scripts/fleet-smoke.mjs`（四账户并行 + 大盘总控，需网络）。

详见 [docs/architecture.md](docs/architecture.md)。
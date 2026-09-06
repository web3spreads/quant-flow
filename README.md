# Quant Flow

AI 驱动的 [Hyperliquid](https://hyperliquid.xyz) 网格交易系统，形态为[DeepSeek Harness（dsh）](https://github.com/deepseek-ai/deepseek-harness)插件。**LLM 只产出结构化 JSON 决策，下单、止盈止损与风控永远由确定性代码完成**，LLM 故障绝不放大成交易动作。

**策略只有网格一种**：决策后端默认是**规则后端**（`llm.provider: rule`，与回测同源，每周期给出 UPDATE_GRID、真正是否重建由重建闸门定夺，不发任何外部请求）；可选让 LLM 低频判形态（要不要更新、往哪个方向、多宽），此时启动即告警「LLM 在交易回路中」并受每日调用上限约束。数学引擎按波动率与账户实际费率算价位与金额，GridManager 管理层级生命周期——批量 post-only 布单、开→平闭环、持库存不重心化、库存守卫与自动库存上限、Triple Barrier 兜底。

**多账户并行**：`accounts` 配置任意数量的「地址 × 环境 × 交易对」组合同时运行，数量不设上限；每账户独立引擎、私钥环境变量、状态与日志目录、保护链，互不干扰（同地址同环境双开拒绝启动）。按 `fleet.start_stagger_secs`（默认 2s）错峰启动，避免 N 台引擎对同一出口 IP 齐射撞限流。

**内置看板**（默认 http://127.0.0.1:3181/ ）：大盘对比、每账户运行数据与决策时间线、网格层级阶梯、回测报告，以及全部配置的网页设置（表单由 Schema 自动生成，保存后热重配）。

## 使用

```bash
export HYPERLIQUID_PRIVATE_KEY=0x...       # 必填，交易钱包或 API 钱包私钥
export HYPERLIQUID_TESTNET=true            # 默认测试网；主网须显式 false

dsh plugin --profile trading add dsh-plugin-quant-flow
dsh --profile trading                      # 看板在 http://127.0.0.1:3181/
```

其它环境变量：`HYPERLIQUID_ACCOUNT_ADDRESS`（仅 API 钱包模式）、`LLM_API_KEY`（仅 `llm.provider=openai` 时）、`QUANTFLOW_WEB_TOKEN`（看板监听非 127.0.0.1 时必设）；主网账户另需 `QUANTFLOW_MAINNET_MAX_NOTIONAL_USD` 与 `QUANTFLOW_MAINNET_ACK`（见下文「主网双重闸」）。

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

## 主网双重闸

测试网与主网只差 `HYPERLIQUID_TESTNET` 一个变量，所以主网账户（单账户 `HYPERLIQUID_TESTNET=false`，或多账户条目 `testnet: false`）额外要求两条都满足，缺一即拒绝启动：

1. **名义额硬上限** `QUANTFLOW_MAINNET_MAX_NOTIONAL_USD`（> 0）。引擎把交易所客户端包在一道闸里：任何一批开仓单提交前，用「全部持仓名义额 + 非 reduce_only 挂单名义额 + 本批拟挂名义额」对比上限，超限整批开仓单拒绝并打严重日志；reduce_only（平仓单、保护单、紧急平仓）永远放行；持仓或挂单查询失败按 fail-closed 拒绝开仓。看板账户头显示「已用 / 上限」进度条。
2. **配置指纹确认** `QUANTFLOW_MAINNET_ACK`。启动时对本账户生效配置（交易对、杠杆、网格参数、保护链、决策来源、名义额上限、钱包地址；不含任何密钥）算 SHA-256，日志打印 `🔏 配置指纹`；主网账户要求该变量与指纹完全相等。第一次启动会因缺 ACK 被拒并在错误信息里给出指纹，核对后写入变量再启动。任何影响下单的配置变动都会改变指纹——看板配置页的热重配走同一入口，对主网账户会被拒绝（返回 400、不落盘），改主网配置只能「改环境变量 + 重启」。

多账户模式下每个主网账户独立满足以上两条：条目的 `mainnet_max_notional_env` / `mainnet_ack_env` 指定各自的变量名（上限变量可共用，指纹各不相同所以 ACK 变量必须各配一个）。

## 决策来源与 LLM 预算

`llm.provider` 三选一：`rule`（默认，规则后端，零外部请求）、`dsh`（宿主 `ctx.llm`）、`openai`（兼容端点）。后两者把 LLM 放进交易回路，启动即告警，并受 `llm.daily_call_cap`（默认 300，不可关闭）约束：按**后端每一次实际请求**计数（重试每次都算），持久化到 `data/llm-usage.json`（进程重启不归零，UTC 跨日归零），触顶后当天所有周期降级 KEEP_GRID、只告警一次，不触发兜底重建。看板账户头显示「今日调用 / 上限」。多账户各自计数，共用一把 key 时总量是各账户上限之和。

## 盘口录制与数据资产

`scripts/record-book.mjs` 常驻录主网 BTC/ETH/SOL/HYPE 的 l2book（5 档 ~2Hz）/ l2full（20 档）/ trades / bbo / ctx，按本机接收时间在 UTC 零点切日：

- 缺口：任一频道 60 秒无消息告警一次（连接死掉、订阅失败、日切后一片安静都会报），恢复时再记一条；日切输出上一日每频道消息数、最大间隔、缺口数、丢弃数、覆盖秒数、收包延迟（`r − t`）分位数。
- 重连：握手 20 秒超时；90 秒无消息强制丢弃旧连接重连；10 分钟仍无消息则优雅关流后以非零码退出，由 systemd 拉起。判活看 `status.json` 的 `last_message_age_s` / `stale`，不要看 `updatedAt`，心跳在连接死掉时照样新鲜。
- 延迟：每分钟一次只读 RTT 探针，日切写进清单 `rtt_ms`，与收包延迟一起作为研究阶段延迟模型的输入。
- 完整性：日切后对上一日每个文件流式解压计行、算 sha256，写 `<COIN>/<日>/manifest.json`（含上述频道统计）；启动时补做缺清单的历史日。`node scripts/book-verify.mjs --dir data/book` 逐日复核（多成员 gzip、截断、损坏都能识别）。
- 磁盘水位：`data/book` 超过 20 GB 或磁盘可用低于 15% 即告警并暂停录 bbo（可从 l2book 近似重建），回落后自动恢复；trades / l2book 绝不丢。
- 异地备份：`scripts/book-backup.sh` + `deploy/quantflow-bookbackup.{service,timer}` 每日 rsync 已有清单的日目录到 `BOOK_BACKUP_DEST`，本地保留 45 天（仅删除已备份的），远端保留 90 天；`--check` 只看计划。

## 开发

```bash
npm ci && npm run check && npm test        # 类型检查 + vitest
npm run build                              # 编译到 lib/
node scripts/fetch-history.mjs --coin BTC --interval 15m    # 拉历史 K 线
node scripts/backtest-grid.mjs --symbol BTC --interval 15m --compare
node scripts/backtest-suite.mjs            # 整批回测（含不交易/买入持有两条基准），结果进看板「回测」页
node scripts/attribution.mjs --testnet     # 链上成交归因（只读）
node scripts/record-book.mjs --coins BTC,ETH --out data/book   # 盘口录制
node scripts/book-verify.mjs --dir data/book                   # 盘口数据完整性校验
```

**改网格或执行层之前先跑回测对照**，用数字说话；回测用的是生产同一套引擎，撮合偏保守，数字用于比较方案而非预测收益。发布前自测：`node scripts/boot-smoke.mjs`（单账户启动链路）、`node scripts/fleet-smoke.mjs`（四账户并行 + 大盘总控，需网络）。

详见 [docs/architecture.md](docs/architecture.md)。
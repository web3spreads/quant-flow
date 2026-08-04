---
sidebar_position: 2
title: config.yaml 参考
description: 所有 config.yaml 配置项完整参考
---

# config.yaml 参考

`config.yaml` 是永续合约 Agent 的主配置文件。从示例文件复制开始：

```bash
cp config.yaml.example config.yaml
```

## LLM 配置

```yaml
llm:
  client_type: langchain_nvidia   # openai | cloudflare | google | litellm | nvidia
  model: qwen/qwen3-next-80b-a3b-instruct   # 按你的供应商可用模型填写
  temperature: 0.2                 # 交易决策建议使用低温度
```

### `client_type` 支持的值

| 值 | 提供商 | 说明 |
|---|---|---|
| `openai` | OpenAI | 需要 `OPENAI_API_KEY` |
| `langchain_nvidia` | NVIDIA NIM | 需要 `NVIDIA_API_KEY` |
| `google` | Google Gemini | 需要 `GOOGLE_API_KEY` |
| `cloudflare` | Cloudflare Workers AI | 需要 `CLOUDFLARE_*` 变量 |
| `litellm` | LiteLLM 代理 | 灵活的多提供商路由 |

:::tip 温度设置
交易决策建议将 `temperature` 保持在 `0.1`–`0.3`。较高的温度会产生更有创意但可靠性较低的 JSON 输出。
:::

## 交易参数

```yaml
trading:
  # 策略总开关
  perp_enabled: true         # 启用永续合约方向交易 Agent
  grid_enabled: false        # 启用网格交易做市策略

  symbols: [BTC, ETH]       # 使用简单符号，不是交易对格式（不要用 BTC/USDT）
  max_trade_amount: 100     # 单笔交易最大 USD 金额 / 网格投入上限
  max_leverage: 10          # 最大杠杆倍数
  limit_order_enabled: false  # 是否使用限价单入场（vs. 市价单）
```

:::warning 符号格式
请使用 `BTC`、`ETH`——**不是** `BTC/USDT` 或 `BTC-PERP`。Hyperliquid 内部使用简单资产符号。
:::

### 杠杆说明

- 不同资产在 Hyperliquid 上有不同的最大杠杆限制
- 如果 `max_leverage` 超过资产限制，订单会被拒绝
- 建议从保守的 `max_leverage: 3` 或 `5` 开始，确认策略有效后再调整

## 调度器

```yaml
scheduler:
  interval_minutes: 3    # 两次决策之间的兜底轮询间隔
```

调度器是兜底决策循环。当启用[市场监控](../features/market-monitor.md)时，价格异动会立即触发决策，无需等待定时间隔。

## Prompt 策略

```yaml
prompt:
  set: nof1-improved    # 参见下表
```

| 值 | 说明 |
|---|---|
| `default` | 标准 FinCoT 推理链 |
| `conservative` | 趋势有分歧即持有，要求 R:R ≥ 2.0 |
| `aggressive` | 3个条件即可入场，接受 R:R ≥ 1.2 |
| `nof1` | 完整 FinCoT 集成的增强策略 |
| `nof1-improved` | **推荐** — 完整 FinCoT + 增强数据集成 |
| `realtime` | 优先考虑价格行为而非滞后指标 |
| `realtime-eng` | realtime 的英文版 |

## 增强分析

```yaml
enhanced_analysis:
  enabled: true    # 启用 CEX 资金费率、链上数据采集
```

这是 [CEX 信号](../features/cex-signals.md) 和 [Regime 自适应](../features/regime-adaptive.md) 的基础开关。依赖关系：

```
enhanced_analysis.enabled: true
  ├── debate.enabled: true         # 独立开关
  └── regime_adaptive.enabled: true  # 依赖 enhanced_analysis
```

## 辩论 Agent

```yaml
debate:
  enabled: false    # 每次决策额外增加 2 次 LLM 调用
```

详见[多空辩论](../features/debate.md)。

## Regime 自适应

```yaml
regime_adaptive:
  enabled: false
  # 可选参数覆盖：
  # trending:
  #   signal_threshold: 0.5
  #   min_confidence: 0.35
  #   max_leverage: 10
  # ranging:
  #   signal_threshold: 0.75
  #   max_leverage: 5
  # volatile:
  #   signal_threshold: 0.85
  #   max_leverage: 3
```

详见[市场 Regime 自适应](../features/regime-adaptive.md)。

## 账户保护（插件化）

新格式为插件列表，每个插件可独立开关组合。旧的
`account_protection: { enabled: true, ... }` 仍可工作并自动迁移到新格式。

```yaml
# 空列表 = 关闭所有风控
protections:
  - name: max_drawdown
    max_drawdown_pct: 0.10       # 回撤 ≥ 10% → 全部平仓 + 暂停
    pause_hours: 4

  - name: daily_loss
    max_daily_loss_pct: 0.05     # 单日亏损 ≥ 5% → 暂停新开仓
    pause_hours: 4

  - name: consecutive_loss
    max_consecutive_losses: 5
    per_symbol: true             # true = 仅锁定亏损交易对；false = 全局暂停
    pause_hours: 4

  - name: position_timeout
    max_position_hours: 48       # 持仓超过此时长 → 自动平仓
```

:::warning 请保留必要的保护插件
保护插件是关键安全机制。只有充分理解风险时才能禁用。
:::

## 市场监控

```yaml
market_monitor:
  enabled: false
  check_interval_seconds: 30   # 价格检查频率
  alert_threshold_pct: 3.0     # HIGH 告警阈值
  elevated_threshold_pct: 1.5  # ELEVATED 阈值（仅记录日志）
  extreme_threshold_pct: 5.0   # EXTREME 阈值
  cooldown_minutes: 5          # 触发后冷却时间
  reference_window_minutes: 10 # 价格基准窗口
```

详见[市场主动监控](../features/market-monitor.md)。

## 复盘 Agent

```yaml
review_agent:
  # 6a: 双粒度反思
  instant_reflection_enabled: false    # 每次平仓后即时反思
  weekly_reflection_enabled: false     # 每周 LLM 策略复盘
  weekly_reflection_day: 0             # 0=周一
  weekly_reflection_hour: 8

  # 6b: Regime 感知记忆
  regime_aware_enabled: false
  regime_mismatch_factor: 0.4          # Regime 不匹配时的降权因子

  # 6c: 确认偏差防护
  bias_protection_enabled: false
  max_positive_ratio: 0.7              # 记忆库中正面经验的最大比例
  negative_confidence_boost: 1.15      # 负面经验置信度加成

  # 6d: 事实-主观分离
  fact_subjective_split_enabled: false
  trending_subjective_boost: 1.3
  ranging_factual_boost: 1.3

  # 6e: Prompt 元反思
  prompt_meta_reflection_enabled: false
  prompt_optimization_dir: "logs/prompt_optimization"
```

详见[复盘与反思系统](../features/review-system.md)。

## 数据配置

```yaml
data:
  timeframe: 1h    # 主要决策使用的 OHLCV K 线周期
```

## 完整示例

```yaml
llm:
  client_type: langchain_nvidia
  model: qwen/qwen3-next-80b-a3b-instruct
  temperature: 0.2

trading:
  perp_enabled: true
  grid_enabled: false
  symbols: [BTC, ETH]
  max_trade_amount: 100
  max_leverage: 10
  limit_order_enabled: false

scheduler:
  interval_minutes: 3

data:
  timeframe: 1h

prompt:
  set: nof1-improved

enhanced_analysis:
  enabled: true

debate:
  enabled: true

regime_adaptive:
  enabled: true

protections:
  - name: max_drawdown
    max_drawdown_pct: 0.10
    pause_hours: 4
  - name: daily_loss
    max_daily_loss_pct: 0.05
    pause_hours: 4
  - name: consecutive_loss
    max_consecutive_losses: 5
    per_symbol: true
    pause_hours: 4
  - name: position_timeout
    max_position_hours: 48

market_monitor:
  enabled: true
  check_interval_seconds: 30
  alert_threshold_pct: 3.0
  cooldown_minutes: 5

review_agent:
  instant_reflection_enabled: true
  weekly_reflection_enabled: true
  regime_aware_enabled: true
  bias_protection_enabled: true
  fact_subjective_split_enabled: true
  prompt_meta_reflection_enabled: false
```

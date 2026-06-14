---
sidebar_position: 3
title: 网格模式配置
description: 网格做市在统一配置文件 config.yaml 中的配置参考
---

# 网格交易配置

网格交易策略现在直接在统一的 `config.yaml` 配置文件中进行配置，只需将 `trading.grid_enabled` 设置为 `true` 即可启用。

## 交易参数

```yaml
trading:
  # 策略总开关
  perp_enabled: false            # 是否启用永续合约方向交易 Agent
  grid_enabled: true             # 是否启用网格做市交易

  symbols: [ETH]                 # 网格交易的交易对（当前使用列表中的首个币种）
  max_trade_amount: 500          # 总投入上限（USD）
  max_leverage: 5                # 最大杠杆

  # 网格订单功能
  grid_limit_order_take_profit_enabled: true   # 网格成交后是否自动挂止盈单
  grid_limit_order_stop_loss_enabled: true     # 网格成交后是否自动挂止损单
  grid_reduce_only_exit_orders_enabled: true   # 是否启用分层 reduce-only 退出单（分批止盈）
```

### 退出单模式说明

| 选项 | 说明 |
|---|---|
| `grid_limit_order_take_profit_enabled` | 每个网格层限价单成交后，自动在下一个网格边界处下挂限价止盈单 |
| `grid_limit_order_stop_loss_enabled` | 每个网格层限价单成交后，自动在成交价的止损阈值处挂止损单 |
| `grid_reduce_only_exit_orders_enabled` | 使用分层 reduce-only 退出单代替一次性全平，减少大仓位的滑点 |

:::tip
建议开启所有三种退出单模式以获得全面保护。`grid_reduce_only_exit_orders_enabled` 对大仓位尤为重要。
:::

## Agent（AI 决策）参数

```yaml
agent:
  grid_width:
    min_pct: 0.02           # 最小网格间距（占价格的百分比，2%）
    max_pct: 0.15           # 最大网格间距（15%）
    fallback_pct: 0.05      # 数据异常时的回退宽度（5%）
    ai_blend_weight: 0.35   # AI 输出在最终结果中的权重
```

### 网格宽度融合

最终网格宽度是加权融合的结果：

```
最终宽度 = (市场数据宽度 × 0.65) + (AI 建议宽度 × 0.35)
```

市场数据部分使用 ATR 和波动率计算数据驱动的宽度。AI 部分允许 LLM 根据更广泛的背景（新闻、情绪、Regime）进行调整。65/35 的融合比例确保数学引擎主导，同时 AI 提供方向细节。

## LLM 配置

```yaml
llm:
  client_type: langchain_openai
  model: qwen/qwen3.5-122b-a10b
  temperature: 0.3               # 网格模式下建议温度略高 — 提供更弹性的宽度估计
```

## Scheduler 调度

```yaml
scheduler:
  interval_minutes: 5    # 网格重新评估的间隔周期（分钟）
```

网格决策每 5 分钟运行一次。在两次决策间隔之间，GridManager 会自动监控成交并挂出退出单 — 成交处理无需 LLM 调用。

## 数据配置

```yaml
data:
  timeframe: 15m    # 较短的周期适合网格做市 — 对价格波动更敏感
```

:::info
相比于永续合约 Agent 推荐的 1h 周期，网格交易更受益于 15m 或 5m 等较短的周期，能够获得更细粒度的波动率估计来调整网格宽度。
:::

## 完整配置示例

```yaml
llm:
  client_type: langchain_openai
  model: qwen/qwen3.5-122b-a10b
  temperature: 0.3

trading:
  perp_enabled: false
  grid_enabled: true
  symbols: [ETH]
  max_trade_amount: 500
  max_leverage: 5
  grid_limit_order_take_profit_enabled: true
  grid_limit_order_stop_loss_enabled: true
  grid_reduce_only_exit_orders_enabled: true

agent:
  grid_width:
    min_pct: 0.02
    max_pct: 0.15
    fallback_pct: 0.05
    ai_blend_weight: 0.35

scheduler:
  interval_minutes: 5

data:
  timeframe: 15m
```

## 网格状态持久化

网格管理器在每次订单操作后，将当前网格的挂单状态原子化写入 `grid_state.json` 文件。如果机器人重启，它将自动读取此状态文件并与交易所当前挂单进行对齐同步。

:::warning 切勿手动编辑 grid_state.json
手动编辑可能会导致网格管理器与交易所状态失联，引发孤儿订单或重复挂单问题。
:::

## 安全保护机制

GridManager 包含多重内置安全保护：

- **孤儿 Trigger 挂单清理** — 自动检测并撤销无持仓对应的止盈止损单
- **分层 Reduce-only 平仓** — 分步止盈以防大规模市价单产生的严重滑点
- **撤单 20 秒硬超时** — 撤单如在 20 秒内未获链上确认，立即重试并告警
- **状态文件原子写入** — 采用临时文件写入后更名方式，防止进程中断导致 JSON 文件损坏
